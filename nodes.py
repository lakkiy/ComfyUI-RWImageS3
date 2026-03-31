"""
ComfyUI nodes for reading media from S3 and saving images/videos back to S3.

Highlights:
- ReadImageFromS3 reads a normal image from S3
- If the downloaded file is actually a video / Apple Live Photo video, it uses
  ffmpeg and returns the first frame as a ComfyUI IMAGE
- SaveImageToS3 uploads an IMAGE tensor as PNG
- SaveVideoToS3 uploads a local video file

Version: 3.1.0
"""

import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, Tuple, Union

import boto3
import numpy as np
import torch
from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError

# Optional: lets Pillow read HEIC/HEIF files, even if the S3 key says .png/.jpg.
# If pillow-heif is not installed, HEIC/HEIF files will fall back to ffmpeg logic below.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    register_heif_opener = None


# Load environment variables from .env
load_dotenv()


# Primary S3 config
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
ENDPOINT_URL = os.getenv("ENDPOINT_URL")

# Optional fallback S3 config for reads
FALLBACK_AWS_ACCESS_KEY = os.getenv("FALLBACK_AWS_ACCESS_KEY_ID")
FALLBACK_AWS_SECRET_KEY = os.getenv("FALLBACK_AWS_SECRET_ACCESS_KEY")
FALLBACK_AWS_REGION = os.getenv("FALLBACK_AWS_REGION", "us-east-1")
FALLBACK_S3_BUCKET_NAME = os.getenv("FALLBACK_S3_BUCKET_NAME")
FALLBACK_ENDPOINT_URL = os.getenv("FALLBACK_ENDPOINT_URL")

# SaveVideoToS3 only accepts these local video extensions
ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif")

# Optional: used by ReadImageFromS3 when the file is actually a video
FFMPEG_PATH = shutil.which("ffmpeg")


# Primary S3 client
s3_client_config = {
    "aws_access_key_id": AWS_ACCESS_KEY,
    "aws_secret_access_key": AWS_SECRET_KEY,
    "region_name": AWS_REGION,
}
if ENDPOINT_URL:
    s3_client_config["endpoint_url"] = ENDPOINT_URL

s3_client = boto3.client("s3", **s3_client_config)


# Optional fallback S3 client
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


class ReadImageFromS3:
    """
    Read one file from S3 and return it as a ComfyUI IMAGE.

    Flow:
    1. Download from primary S3
    2. If needed, try fallback S3
    3. Try to read it as an image
       - normal image: read it directly
       - animated image / HEIF: use the first frame
    4. If that fails, use ffmpeg and take the first frame
       - useful for mp4/mov and other video-like uploads
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "s3_key": (
                    "STRING",
                    {"default": "input/example.png", "multiline": False},
                ),
            }
        }

    CATEGORY = "image"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "load_image"

    def _image_to_tensor(self, pil_image: Image.Image) -> torch.Tensor:
        pil_image = ImageOps.exif_transpose(pil_image)
        rgb_image = pil_image.convert("RGB")
        image_array = np.array(rgb_image).astype(np.float32) / 255.0
        return torch.from_numpy(image_array)[None, ...]

    def _read_image_file(self, file_path: str) -> torch.Tensor:
        with Image.open(file_path) as pil_image:
            if getattr(pil_image, "is_animated", False):
                pil_image.seek(0)
                return self._image_to_tensor(pil_image.copy())

            return self._image_to_tensor(pil_image)

    def _read_first_video_frame(self, file_path: str, s3_key: str) -> torch.Tensor:
        if not FFMPEG_PATH:
            raise RuntimeError(
                "ffmpeg is required to decode video/live photo inputs, but it was not found in PATH"
            )

        frame_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        frame_path = frame_file.name
        frame_file.close()

        try:
            result = subprocess.run(
                [
                    FFMPEG_PATH,
                    "-nostdin",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    file_path,
                    "-frames:v",
                    "1",
                    frame_path,
                ],
                capture_output=True,
                text=True,
            )

            if (
                result.returncode != 0
                or not os.path.exists(frame_path)
                or os.path.getsize(frame_path) == 0
            ):
                ffmpeg_error = (result.stderr or "").strip() or "unknown ffmpeg error"
                raise RuntimeError(
                    f"ffmpeg could not extract a first frame for '{s3_key}': {ffmpeg_error}"
                )

            return self._read_image_file(frame_path)
        finally:
            if os.path.exists(frame_path):
                os.remove(frame_path)

    def _download_to_temp_file(self, s3_key: str) -> str:
        if not S3_BUCKET_NAME or not S3_BUCKET_NAME.strip():
            raise RuntimeError(
                "ReadImageFromS3: S3_BUCKET_NAME is not configured. Please set it in your .env file."
            )

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=os.path.splitext(s3_key)[1] or ".bin",
        )
        temp_path = temp_file.name
        temp_file.close()

        try:
            start_time = time.time()
            s3_client.download_file(S3_BUCKET_NAME, s3_key, temp_path)
            print(
                f"S3 download completed (primary): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
            )
            return temp_path
        except Exception as primary_error:
            print(f"Primary S3 download failed for '{s3_key}': {primary_error}")

            if fallback_s3_client is None:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise RuntimeError(
                    f"ReadImageFromS3: Failed to download '{s3_key}': {primary_error}"
                )

            try:
                print(f"Attempting fallback S3 download for '{s3_key}'...")
                start_time = time.time()
                fallback_s3_client.download_file(fallback_s3_bucket, s3_key, temp_path)
                print(
                    f"S3 download completed (fallback): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
                )
                return temp_path
            except Exception as fallback_error:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                print(
                    f"Fallback S3 download also failed for '{s3_key}': {fallback_error}"
                )
                raise RuntimeError(
                    f"ReadImageFromS3: Failed to download '{s3_key}' from both primary and fallback storage. "
                    f"Primary error: {primary_error}. Fallback error: {fallback_error}"
                )

    def load_image(self, s3_key: str) -> Tuple[torch.Tensor]:
        s3_key = (s3_key or "").strip()
        temp_path = self._download_to_temp_file(s3_key)

        try:
            image_read_error = None

            # Step 1: image / animated image / HEIF
            try:
                return (self._read_image_file(temp_path),)
            except (UnidentifiedImageError, OSError, ValueError) as error:
                image_read_error = str(error)
                print(
                    f"ReadImageFromS3: PIL decode failed for '{s3_key}', attempting first-frame fallback: {image_read_error}"
                )

            # Step 2: video / live photo video first frame
            try:
                image_tensor = self._read_first_video_frame(temp_path, s3_key)
                print(f"ReadImageFromS3: First-frame fallback succeeded for '{s3_key}'")
                return (image_tensor,)
            except Exception as frame_error:
                raise RuntimeError(
                    f"ReadImageFromS3: Failed to process '{s3_key}' as either an image or a video/live photo. "
                    f"Image read error: {image_read_error}. Video/live photo fallback error: {frame_error}"
                )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

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
    """Save one ComfyUI IMAGE tensor to S3 as a PNG file."""

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

        image_tensor = image[0] if image.ndim == 4 else image
        image_array = image_tensor.detach().cpu().numpy()
        image_array = np.clip(image_array * 255.0, 0, 255).astype(np.uint8)
        pil_image = Image.fromarray(image_array)

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                pil_image.save(temp_file.name, format="PNG")
                temp_file_path = temp_file.name

            start_time = time.time()
            s3_client.upload_file(temp_file_path, S3_BUCKET_NAME, s3_key)
            print(
                f"S3 upload completed (image): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
            )
        except Exception as error:
            raise RuntimeError(
                f"SaveImageToS3: Failed to upload to '{s3_key}': {error}"
            )
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

        return ()


class SaveVideoToS3:
    """Upload one local video file to S3."""

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

        extension = os.path.splitext(local_video_path)[1].lower()
        if extension and extension not in ALLOWED_VIDEO_EXTENSIONS:
            raise RuntimeError(
                f"SaveVideoToS3: Unsupported video extension '{extension}'. "
                f"Allowed: {ALLOWED_VIDEO_EXTENSIONS}"
            )

        try:
            start_time = time.time()
            s3_client.upload_file(local_video_path, S3_BUCKET_NAME, s3_key)
            print(
                f"S3 upload completed (video): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
            )
        except Exception as error:
            raise RuntimeError(
                f"SaveVideoToS3: Failed to upload '{local_video_path}' to '{s3_key}': {error}"
            )

        return ()


class IsMaskEmptyNode:
    """Return True if every value in the mask is 0."""

    @classmethod
    def INPUT_TYPES(cls):
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


# Names shown in ComfyUI
NODE_CLASS_MAPPINGS = {
    "Read Image From S3": ReadImageFromS3,
    "Save Image To S3": SaveImageToS3,
    "Save Video To S3": SaveVideoToS3,
    "Is Mask Empty": IsMaskEmptyNode,
}
