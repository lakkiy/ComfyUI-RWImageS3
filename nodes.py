"""
ComfyUI S3 Image Processing Nodes

This module provides custom ComfyUI nodes for reading and saving images from/to AWS S3 storage.
It includes two main node classes:
- ReadImageFromS3: Loads images from S3 input folder
- SaveImageToS3: Saves processed images to S3 output folder

Requirements:
- boto3 (AWS SDK)
- torch (PyTorch)
- PIL (Python Imaging Library)
- python-dotenv
- numpy

Author: Jayanth Yedureswaram
GitHub: https://github.com/Jayanth-y
Version: 1.0
"""

import os
import boto3
import torch
import tempfile
import numpy as np
from PIL import Image, ImageOps
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Tuple, Union, Dict, Any


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

# Supported image file extensions (single-frame formats only)
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff")

# Initialize AWS S3 client with credentials
s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION,
)


# ═════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def list_images_from_s3(prefix: str = "input/") -> List[str]:
    """
    Retrieve a list of supported image files from the specified S3 prefix.
    
    This function scans the S3 bucket for objects under the given prefix and
    filters them to include only files with supported image extensions.
    
    Args:
        prefix (str): S3 prefix to search for images (default: "input/")
        
    Returns:
        List[str]: Sorted list of image filenames (without prefix) found in S3.
                   Returns empty list if no images found or if S3 operation fails.
                   
    Note:
        - Only includes files with extensions defined in ALLOWED_EXTENSIONS
        - Excludes directories (keys ending with "/")
        - Case-insensitive extension matching
    """
    try:
        # List objects in S3 bucket under the specified prefix
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
    except Exception as e:
        # Return empty list on any S3 operation failure
        print(f"Warning: Failed to list S3 objects: {e}")
        return []

    # Check if any objects were found
    if "Contents" not in response:
        return []

    image_files = []
    
    # Process each object in the S3 response
    for obj in response["Contents"]:
        key = obj["Key"]  # Full S3 object key (e.g., "input/photo.png")
        
        # Skip directories (keys ending with "/")
        if key.endswith("/"):
            continue
            
        # Check if the file has a supported image extension
        key_lower = key.lower()
        for extension in ALLOWED_EXTENSIONS:
            if key_lower.endswith(extension):
                # Extract filename by removing the prefix
                filename = key[len(prefix):]
                if filename:  # Ensure filename is not empty
                    image_files.append(filename)
                break

    return sorted(image_files)


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
                "s3_key": ("STRING", {"default": "input/example.png", "multiline": False}),
            }
        }

    # ComfyUI node metadata
    CATEGORY = "image"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "load_image"

    def load_image(self, s3_key: str) -> Tuple[torch.Tensor]:
        """
        Load and process an image from S3 storage.
        
        This method downloads the specified image from S3 using the full S3 object key,
        applies EXIF orientation correction, converts to RGBA format, extracts RGB channels,
        and normalizes the pixel values to create a PyTorch tensor suitable for ComfyUI processing.
        
        Args:
            s3_key (str): Full S3 object key path (e.g., "my-folder/image.png")
            
        Returns:
            Tuple[torch.Tensor]: Single-element tuple containing the processed image
                                tensor with shape [1, H, W, 3] and values in [0, 1]
                                
        Raises:
            RuntimeError: If S3 download fails or image processing encounters errors
        """
        
        # Create temporary file with appropriate extension for PIL compatibility
        file_extension = os.path.splitext(s3_key)[1]
        with tempfile.NamedTemporaryFile(delete=True, suffix=file_extension) as temp_file:
            try:
                # Download image from S3 to temporary file
                s3_client.download_file(S3_BUCKET_NAME, s3_key, temp_file.name)
            except Exception as e:
                raise RuntimeError(f"ReadImageFromS3: Failed to download '{s3_key}': {e}")

            # Load and process the image using PIL
            try:
                pil_image = Image.open(temp_file.name)
                
                # Apply EXIF orientation correction to handle rotated images
                pil_image = ImageOps.exif_transpose(pil_image)
                
                # Convert to RGBA to ensure consistent 4-channel format
                rgba_image = pil_image.convert("RGBA")
                
                # Convert PIL image to numpy array and normalize to [0, 1]
                image_array = np.array(rgba_image).astype(np.float32) / 255.0  # Shape: [H, W, 4]
                
                # Extract RGB channels (ignore alpha channel)
                rgb_array = image_array[:, :, :3]  # Shape: [H, W, 3]
                
                # Convert to PyTorch tensor and add batch dimension
                image_tensor = torch.from_numpy(rgb_array)[None, ...]  # Shape: [1, H, W, 3]
                
                return (image_tensor,)
                
            except Exception as e:
                raise RuntimeError(f"ReadImageFromS3: Failed to process image '{s3_key}': {e}")

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
        Validate that the specified S3 object exists.
        
        This method is called by ComfyUI to validate inputs before execution.
        It checks that the specified S3 object exists in the bucket.
        
        Args:
            s3_key (str): The S3 object key to validate
            
        Returns:
            bool: True if validation passes
            str: Error message if validation fails
        """
        if not s3_key or not s3_key.strip():
            return "S3 key cannot be empty."
        
        # Check if the file has a supported image extension
        s3_key_lower = s3_key.lower()
        has_valid_extension = any(s3_key_lower.endswith(ext) for ext in ALLOWED_EXTENSIONS)
        if not has_valid_extension:
            return f"File '{s3_key}' does not have a supported image extension. Supported: {', '.join(ALLOWED_EXTENSIONS)}"
        
        # Check if the object exists in S3
        try:
            s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        except Exception as e:
            return f"S3 object '{s3_key}' not found or inaccessible: {str(e)}"
        
        return True


class SaveImageToS3:
    """
    ComfyUI Node: Save Image To S3
    
    This node enables saving processed images from ComfyUI workflows directly to
    AWS S3 storage. It accepts image tensors, converts them to PIL format,
    saves them as PNG files with timestamps, and uploads them to the S3 output folder.
    
    Features:
    - Automatic timestamp generation for unique filenames
    - PNG format with lossless compression
    - Batch dimension handling (supports both [1, H, W, 3] and [H, W, 3])
    - Proper tensor denormalization from [0, 1] to [0, 255]
    - Pass-through image tensor for downstream nodes
    - S3 key return for workflow integration
    
    Input:
    - image: PyTorch tensor containing the image data
    - filename_prefix: Base name for the saved file (timestamp will be appended)
    
    Output:
    - IMAGE: The same input tensor (for connecting to preview or other nodes)
    - STRING: S3 key of the uploaded file (e.g., "output/prefix_20240604123456.png")
    """
    
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        """
        Define the input schema for the ComfyUI node interface.
        
        Returns:
            Dict containing the required input specifications for ComfyUI.
        """
        return {
            "required": {
                "image": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "comfyui_s3_img_connect"}),
            }
        }

    # ComfyUI node metadata
    CATEGORY = "image"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "folder/filename")
    FUNCTION = "save_to_s3"

    def save_to_s3(self, image: torch.Tensor, filename_prefix: str) -> Tuple[torch.Tensor, str]:
        """
        Save an image tensor to S3 storage.
        
        This method processes a PyTorch image tensor, converts it to a PIL Image,
        saves it as a PNG file with a timestamped filename, and uploads it to
        the S3 output folder. The original tensor is returned unchanged for
        downstream node compatibility.
        
        Args:
            image (torch.Tensor): Image tensor of shape [1, H, W, 3] or [H, W, 3]
                                 with pixel values in [0, 1] range
            filename_prefix (str): Base filename (without extension) for the saved image
            
        Returns:
            Tuple[torch.Tensor, str]: 
                - The original image tensor (unchanged)
                - S3 key string of the uploaded file
                
        Raises:
            ValueError: If input image is not a torch.Tensor
            RuntimeError: If S3 upload fails
        """
        # Validate input type
        if not isinstance(image, torch.Tensor):
            raise ValueError("SaveImageToS3: 'image' parameter must be a torch.Tensor")

        # Handle batch dimension - remove if present
        if image.ndim == 4:
            # Input shape: [1, H, W, 3] -> [H, W, 3]
            processed_tensor = image[0]
        else:
            # Input shape: [H, W, 3]
            processed_tensor = image

        # Convert tensor to numpy array and denormalize
        image_array = processed_tensor.detach().cpu().numpy()
        
        # Denormalize from [0, 1] to [0, 255] and convert to uint8
        image_array = np.clip(image_array * 255.0, 0, 255).astype(np.uint8)  # Shape: [H, W, 3]
        
        # Convert numpy array to PIL Image
        pil_image = Image.fromarray(image_array)

        # Create temporary file for PNG conversion
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            try:
                # Save image as PNG format
                pil_image.save(temp_file.name, format="PNG")
                temp_file_path = temp_file.name
            except Exception as e:
                raise RuntimeError(f"SaveImageToS3: Failed to save temporary PNG: {e}")

        # Generate timestamped filename
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        s3_key = f"output/{filename_prefix}_{timestamp}.png"

        try:
            # Upload file to S3
            s3_client.upload_file(temp_file_path, S3_BUCKET_NAME, s3_key)
        except Exception as e:
            # Clean up temporary file on upload failure
            os.remove(temp_file_path)
            raise RuntimeError(f"SaveImageToS3: Failed to upload to '{s3_key}': {e}")

        # Clean up temporary file after successful upload
        os.remove(temp_file_path)

        # Return both the original image tensor and the S3 key
        # This allows downstream nodes (like PreviewImage) to access the image
        # while also providing the S3 location for workflow integration
        return (image, s3_key)


# ═════════════════════════════════════════════════════════════════════════════
# COMFYUI NODE REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

# Node class mappings for ComfyUI discovery
# This dictionary maps the display names shown in ComfyUI to the actual node classes
NODE_CLASS_MAPPINGS = {
    "Read Image From S3": ReadImageFromS3,
    "Save Image To S3": SaveImageToS3,
}

# Optional: Display name mappings (if you want different names in the UI)
# NODE_DISPLAY_NAME_MAPPINGS = {
#     "ReadImageFromS3": "Read Image From S3",
#     "SaveImageToS3": "Save Image To S3",
# }
