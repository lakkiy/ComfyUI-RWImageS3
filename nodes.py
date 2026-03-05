"""
ComfyUI S3 Media Nodes

Custom ComfyUI nodes for reading images from S3 and uploading images/videos to S3.

Included nodes:
- ReadImageFromS3
- SaveImageToS3
- SaveVideoToS3
- IsMaskEmptyNode

Requirements:
- boto3
- torch
- pillow
- python-dotenv
- numpy

GitHub: https://github.com/lakkiy/ComfyUI-RWImageS3
Version: 3.0.0
"""

import os
import tempfile
import time
from typing import Any, Dict, Tuple, Union

import boto3
import numpy as np
import torch
from dotenv import load_dotenv
from PIL import Image, ImageOps

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION AND INITIALIZATION
# ═════════════════════════════════════════════════════════════════════════════

# Load environment variables from .env file (must be located next to this script)
load_dotenv()

# AWS Configuration - Retrieved from environment variables
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")  # Default to us-east-1
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
ENDPOINT_URL = os.getenv(
    "ENDPOINT_URL"
)  # Custom S3-compatible endpoint (optional, e.g., Cloudflare R2)

# Fallback S3 Configuration (optional) - Retrieved from environment variables
FALLBACK_AWS_ACCESS_KEY = os.getenv("FALLBACK_AWS_ACCESS_KEY_ID")
FALLBACK_AWS_SECRET_KEY = os.getenv("FALLBACK_AWS_SECRET_ACCESS_KEY")
FALLBACK_AWS_REGION = os.getenv("FALLBACK_AWS_REGION", "us-east-1")
FALLBACK_S3_BUCKET_NAME = os.getenv("FALLBACK_S3_BUCKET_NAME")
FALLBACK_ENDPOINT_URL = os.getenv("FALLBACK_ENDPOINT_URL")

# Supported image file extensions (single-frame formats only)
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff")

# Supported video file extensions for SaveVideoToS3
ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif")

# Initialize AWS S3 client with credentials
# If ENDPOINT_URL is set, use it for S3-compatible services (e.g., Cloudflare R2, MinIO)
s3_client_config = {
    "aws_access_key_id": AWS_ACCESS_KEY,
    "aws_secret_access_key": AWS_SECRET_KEY,
    "region_name": AWS_REGION,
}
if ENDPOINT_URL:
    s3_client_config["endpoint_url"] = ENDPOINT_URL

s3_client = boto3.client("s3", **s3_client_config)

# Initialize fallback S3 client if fallback credentials are provided
fallback_s3_client = None
fallback_s3_bucket = None
if FALLBACK_AWS_ACCESS_KEY and FALLBACK_AWS_SECRET_KEY and FALLBACK_S3_BUCKET_NAME:
    fallback_s3_client_config = {
        "aws_access_key_id": FALLBACK_AWS_ACCESS_KEY,
        "aws_secret_access_key": FALLBACK_AWS_SECRET_KEY,
        "region_name": FALLBACK_AWS_REGION,
    }
    if FALLBACK_ENDPOINT_URL:
        fallback_s3_client_config["endpoint_url"] = FALLBACK_ENDPOINT_URL

    fallback_s3_client = boto3.client("s3", **fallback_s3_client_config)
    fallback_s3_bucket = FALLBACK_S3_BUCKET_NAME
    print(f"Fallback S3 client initialized: bucket={FALLBACK_S3_BUCKET_NAME}")


# ═════════════════════════════════════════════════════════════════════════════
# COMFYUI NODE CLASSES
# ═════════════════════════════════════════════════════════════════════════════


class ReadImageFromS3:
    """
    ComfyUI Node: Read Image From S3

    This node provides functionality to load images directly from AWS S3 storage
    into ComfyUI workflows using custom S3 object keys. Users can specify the full
    S3 path to their image files, allowing for flexible folder structures.
    The node processes images through PIL for proper orientation and format conversion,
    and returns them as normalized PyTorch tensors ready for ComfyUI processing.

    Features:
    - Custom S3 path input (user can specify any S3 object key)
    - Automatic EXIF orientation correction
    - RGBA to RGB conversion with proper alpha handling
    - Tensor normalization to [0, 1] range
    - S3 object existence validation
    - Error handling for network and file operations

    Input:
    - s3_key: Text input for full S3 object key (e.g., "my-folder/image.png")

    Output:
    - IMAGE: PyTorch tensor of shape [1, H, W, 3] with RGB values in [0, 1] range
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        """
        Define the input schema for the ComfyUI node interface.

        Returns:
            Dict containing the required input specifications for ComfyUI.
            The s3_key parameter allows users to specify the full S3 object path.
        """
        return {
            "required": {
                "s3_key": (
                    "STRING",
                    {"default": "input/example.png", "multiline": False},
                ),
            }
        }

    # ComfyUI node metadata
    CATEGORY = "image"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "load_image"

    def load_image(self, s3_key: str) -> Tuple[torch.Tensor]:
        """
        Load and process an image from S3 storage with fallback support.

        This method downloads the specified image from S3 using the full S3 object key,
        applies EXIF orientation correction, converts to RGBA format, extracts RGB channels,
        and normalizes the pixel values to create a PyTorch tensor suitable for ComfyUI processing.

        If the primary S3 storage fails to retrieve the image (e.g., 404 not found),
        the method automatically attempts to download from the fallback S3 storage if configured.

        Args:
            s3_key (str): Full S3 object key path (e.g., "my-folder/image.png")

        Returns:
            Tuple[torch.Tensor]: Single-element tuple containing the processed image
                                tensor with shape [1, H, W, 3] and values in [0, 1]

        Raises:
            RuntimeError: If S3 download fails from both primary and fallback storage,
                         or if image processing encounters errors
        """

        # Create temporary file with appropriate extension for PIL compatibility
        file_extension = os.path.splitext(s3_key)[1]
        with tempfile.NamedTemporaryFile(
            delete=True, suffix=file_extension
        ) as temp_file:
            # Try primary S3 storage first
            download_successful = False
            primary_error = None

            try:
                # Download image from primary S3 to temporary file
                start_time = time.time()
                s3_client.download_file(S3_BUCKET_NAME, s3_key, temp_file.name)
                end_time = time.time()
                download_duration = end_time - start_time
                print(
                    f"S3 download completed (primary): s3_key='{s3_key}', duration={download_duration:.3f}s"
                )
                download_successful = True
            except Exception as e:
                primary_error = e
                print(f"Primary S3 download failed for '{s3_key}': {e}")

                # Try fallback S3 storage if configured
                if fallback_s3_client is not None:
                    try:
                        print(f"Attempting fallback S3 download for '{s3_key}'...")
                        start_time = time.time()
                        fallback_s3_client.download_file(
                            fallback_s3_bucket, s3_key, temp_file.name
                        )
                        end_time = time.time()
                        download_duration = end_time - start_time
                        print(
                            f"S3 download completed (fallback): s3_key='{s3_key}', duration={download_duration:.3f}s"
                        )
                        download_successful = True
                    except Exception as fallback_e:
                        print(
                            f"Fallback S3 download also failed for '{s3_key}': {fallback_e}"
                        )
                        raise RuntimeError(
                            f"ReadImageFromS3: Failed to download '{s3_key}' from both primary and fallback storage. "
                            f"Primary error: {primary_error}. Fallback error: {fallback_e}"
                        )
                else:
                    # No fallback configured, raise the primary error
                    raise RuntimeError(
                        f"ReadImageFromS3: Failed to download '{s3_key}': {primary_error}"
                    )

            if not download_successful:
                raise RuntimeError(
                    f"ReadImageFromS3: Failed to download '{s3_key}': {primary_error}"
                )

            # Load and process the image using PIL
            try:
                pil_image = Image.open(temp_file.name)

                # Apply EXIF orientation correction to handle rotated images
                pil_image = ImageOps.exif_transpose(pil_image)

                # Convert to RGBA to ensure consistent 4-channel format
                rgba_image = pil_image.convert("RGBA")

                # Convert PIL image to numpy array and normalize to [0, 1]
                image_array = (
                    np.array(rgba_image).astype(np.float32) / 255.0
                )  # Shape: [H, W, 4]

                # Extract RGB channels (ignore alpha channel)
                rgb_array = image_array[:, :, :3]  # Shape: [H, W, 3]

                # Convert to PyTorch tensor and add batch dimension
                image_tensor = torch.from_numpy(rgb_array)[
                    None, ...
                ]  # Shape: [1, H, W, 3]

                return (image_tensor,)

            except Exception as e:
                raise RuntimeError(
                    f"ReadImageFromS3: Failed to process image '{s3_key}': {e}"
                )

    @classmethod
    def IS_CHANGED(cls, s3_key: str) -> str:
        """
        ComfyUI cache invalidation method.

        This method helps ComfyUI determine when to re-execute the node.
        Returning the S3 key ensures the node re-executes when
        a different S3 object is specified.

        Args:
            s3_key (str): The S3 object key

        Returns:
            str: The S3 key for cache comparison
        """
        return s3_key

    @classmethod
    def VALIDATE_INPUTS(cls, s3_key: str) -> Union[bool, str]:
        """
        Validate that the S3 key is not empty.

        This method only performs basic validation to allow lazy evaluation
        scenarios where the node may not be executed (e.g., switch nodes).
        S3 existence checks are performed at execution time in load_image.

        Args:
            s3_key (str): The S3 object key to validate

        Returns:
            bool: True if validation passes
            str: Error message if validation fails
        """
        if not s3_key or not s3_key.strip():
            return "S3 key cannot be empty."
        return True


class SaveImageToS3:
    """Save an IMAGE tensor to S3 using an explicit s3_key (folder + filename)."""

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
                "s3_key": (
                    "STRING",
                    {"default": "images/result.png", "multiline": False},
                ),
            }
        }

    CATEGORY = "image"
    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_to_s3"
    OUTPUT_NODE = True

    def save_to_s3(self, image: torch.Tensor, s3_key: str):
        if not isinstance(image, torch.Tensor):
            raise ValueError("SaveImageToS3: 'image' parameter must be a torch.Tensor")

        s3_key = (s3_key or "").strip().lstrip("/")
        if not s3_key:
            raise RuntimeError("SaveImageToS3: s3_key cannot be empty.")

        if not S3_BUCKET_NAME or not S3_BUCKET_NAME.strip():
            raise RuntimeError(
                "SaveImageToS3: S3_BUCKET_NAME is not configured. Please set it in your .env file."
            )

        processed_tensor = image[0] if image.ndim == 4 else image
        image_array = processed_tensor.detach().cpu().numpy()
        image_array = np.clip(image_array * 255.0, 0, 255).astype(np.uint8)
        pil_image = Image.fromarray(image_array)

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                pil_image.save(temp_file.name, format="PNG")
                temp_file_path = temp_file.name

            start_time = time.time()
            s3_client.upload_file(temp_file_path, S3_BUCKET_NAME, s3_key)
            end_time = time.time()
            upload_duration = end_time - start_time
            print(
                f"S3 upload completed (image): s3_key='{s3_key}', duration={upload_duration:.3f}s"
            )
        except Exception as e:
            raise RuntimeError(f"SaveImageToS3: Failed to upload to '{s3_key}': {e}")
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

        return ()


class SaveVideoToS3:
    """Upload a local video file to S3 using an explicit s3_key (folder + filename)."""

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "video_path": (
                    "STRING",
                    {
                        "forceInput": True,
                    },
                ),
                "s3_key": (
                    "STRING",
                    {"default": "videos/result.mp4", "multiline": False},
                ),
            }
        }

    CATEGORY = "image"
    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_video_to_s3"
    OUTPUT_NODE = True

    def save_video_to_s3(self, video_path: str, s3_key: str):
        if not S3_BUCKET_NAME or not S3_BUCKET_NAME.strip():
            raise RuntimeError(
                "SaveVideoToS3: S3_BUCKET_NAME is not configured. Please set it in your .env file."
            )

        local_video_path = os.path.expanduser(str(video_path).strip().strip("\"'"))
        s3_key = (s3_key or "").strip().lstrip("/")

        if not s3_key:
            raise RuntimeError("SaveVideoToS3: s3_key cannot be empty.")

        if not os.path.isabs(local_video_path):
            raise RuntimeError(
                "SaveVideoToS3: Please provide an absolute local video path, "
                "e.g. /root/autodl-tmp/ComfyUI/output/your_video.mp4"
            )

        if not os.path.isfile(local_video_path):
            raise RuntimeError(
                f"SaveVideoToS3: Local video file not found: '{local_video_path}'"
            )

        ext = os.path.splitext(local_video_path)[1].lower()
        if ext and ext not in ALLOWED_VIDEO_EXTENSIONS:
            raise RuntimeError(
                f"SaveVideoToS3: Unsupported video extension '{ext}'. "
                f"Allowed: {ALLOWED_VIDEO_EXTENSIONS}"
            )

        try:
            start_time = time.time()
            s3_client.upload_file(local_video_path, S3_BUCKET_NAME, s3_key)
            end_time = time.time()
            upload_duration = end_time - start_time
            print(
                f"S3 upload completed (video): s3_key='{s3_key}', duration={upload_duration:.3f}s"
            )
        except Exception as e:
            raise RuntimeError(
                f"SaveVideoToS3: Failed to upload '{local_video_path}' to '{s3_key}': {e}"
            )

        return ()


# ═════════════════════════════════════════════════════════════════════════════
# UTILITY NODES
# ═════════════════════════════════════════════════════════════════════════════


class IsMaskEmptyNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("is_empty",)
    FUNCTION = "main"

    def main(self, mask):
        return (bool(torch.all(mask == 0)),)


# ═════════════════════════════════════════════════════════════════════════════
# COMFYUI NODE REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

# Node class mappings for ComfyUI discovery
# This dictionary maps the display names shown in ComfyUI to the actual node classes
NODE_CLASS_MAPPINGS = {
    "Read Image From S3": ReadImageFromS3,
    "Save Image To S3": SaveImageToS3,
    "Save Video To S3": SaveVideoToS3,
    "Is Mask Empty": IsMaskEmptyNode,
}

# Optional: Display name mappings (if you want different names in the UI)
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "ReadImageFromS3": "Read Image From S3",
#     "SaveImageToS3": "Save Image To S3",
# }
