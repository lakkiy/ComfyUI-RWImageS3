"""
ComfyUI nodes for reading media from S3 and saving images/videos back to S3.

Highlights:
- ReadImageFromS3 reads a normal image from S3
- If the downloaded file is actually a video / Apple Live Photo video, it uses
  ffmpeg and returns the first frame as a ComfyUI IMAGE
- SaveImageToS3 uploads an IMAGE tensor as PNG
- SaveVideoToS3 uploads a native VIDEO, a VideoHelperSuite Video Combine
  output, or a local video file

Version: 3.2.0
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
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


# S3 key sets. The first one has no number; extra ones are S3_2_*, S3_3_*, ...
# "auto" tries them in that order:
#   S3_NAME=aws                   # optional, name shown in the node dropdown
#   S3_ACCESS_KEY_ID=...
#   S3_SECRET_ACCESS_KEY=...
#   S3_REGION=us-east-1           # optional
#   S3_BUCKET=my-bucket           # required, a set without it is skipped
#   S3_ENDPOINT_URL=...           # optional (R2, MinIO, ...)
#   S3_2_NAME=r2
#   S3_2_...
# Gaps are fine (S3_2, S3_4 works).
#
# Old-style variables still work and come first, in their old order:
#   AWS_ACCESS_KEY_ID / ... / S3_BUCKET_NAME           -> name "primary"
#   FALLBACK_*, FALLBACK_2_*, FALLBACK_3_*, ...        -> "fallback 1", "fallback 2", ...

# Dropdown value meaning "try every key set in order" (the old behavior)
AUTO_SOURCE = "auto (try in order)"

# How far to scan for S3_<N>_* key sets
MAX_NUMBERED_SOURCES = 99


def _make_source(
    name: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str,
    endpoint_url: str,
) -> Dict[str, Any]:
    client_config = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region or "us-east-1",
    }
    if endpoint_url:
        client_config["endpoint_url"] = endpoint_url

    return {
        "name": name,
        "client": boto3.client("s3", **client_config),
        "bucket": bucket,
    }


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _load_legacy_sources():
    sources = []

    # Primary keeps working with the default boto3 credential chain if keys
    # are not in .env, so only the bucket name is required.
    if _env("S3_BUCKET_NAME"):
        sources.append(
            _make_source(
                "primary",
                _env("S3_BUCKET_NAME"),
                os.getenv("AWS_ACCESS_KEY_ID"),
                os.getenv("AWS_SECRET_ACCESS_KEY"),
                _env("AWS_REGION"),
                _env("ENDPOINT_URL"),
            )
        )

    index = 1
    while True:
        prefix = "FALLBACK_" if index == 1 else f"FALLBACK_{index}_"
        bucket = _env(f"{prefix}S3_BUCKET_NAME")
        access_key = os.getenv(f"{prefix}AWS_ACCESS_KEY_ID")
        secret_key = os.getenv(f"{prefix}AWS_SECRET_ACCESS_KEY")
        if not (bucket and access_key and secret_key):
            # Stop at the first gap, same as before
            break
        sources.append(
            _make_source(
                f"fallback {index}",
                bucket,
                access_key,
                secret_key,
                _env(f"{prefix}AWS_REGION"),
                _env(f"{prefix}ENDPOINT_URL"),
            )
        )
        index += 1

    return sources


def _load_numbered_sources():
    sources = []
    # First set: S3_* (no number). Then S3_1_*, S3_2_*, ...
    prefixes = [("S3_", "default")] + [
        (f"S3_{index}_", f"s3_{index}") for index in range(1, MAX_NUMBERED_SOURCES + 1)
    ]
    for prefix, default_name in prefixes:
        bucket = _env(f"{prefix}BUCKET")
        if not bucket:
            continue
        sources.append(
            _make_source(
                _env(f"{prefix}NAME") or default_name,
                bucket,
                os.getenv(f"{prefix}ACCESS_KEY_ID"),
                os.getenv(f"{prefix}SECRET_ACCESS_KEY"),
                _env(f"{prefix}REGION"),
                _env(f"{prefix}ENDPOINT_URL"),
            )
        )
    return sources


def _load_s3_sources():
    sources = _load_legacy_sources() + _load_numbered_sources()

    # Names must be unique to be selectable; suffix duplicates
    seen = set()
    for source in sources:
        name = source["name"]
        if name == AUTO_SOURCE or name in seen:
            suffix = 2
            while f"{name} ({suffix})" in seen:
                suffix += 1
            print(
                f"S3 source name '{name}' is duplicated, renamed to '{name} ({suffix})'"
            )
            name = f"{name} ({suffix})"
            source["name"] = name
        seen.add(name)
        print(f"S3 source initialized: name='{name}', bucket={source['bucket']}")

    return sources


S3_SOURCES = _load_s3_sources()
S3_SOURCE_NAMES = [source["name"] for source in S3_SOURCES]


def _get_source(name: str, node_name: str) -> Dict[str, Any]:
    for source in S3_SOURCES:
        if source["name"] == name:
            return source
    raise RuntimeError(
        f"{node_name}: S3 source '{name}' is not configured. "
        f"Available: {S3_SOURCE_NAMES or 'none (check your .env file)'}"
    )


def _default_write_source_name() -> str:
    # Writes used the primary storage before; keep that as the default
    return S3_SOURCE_NAMES[0] if S3_SOURCE_NAMES else ""


def _write_source_input() -> Tuple[list, Dict[str, Any]]:
    return (
        S3_SOURCE_NAMES or [""],
        {
            "default": _default_write_source_name(),
            "tooltip": "Which S3 key set to upload with. Defaults to the first configured one.",
        },
    )


def _resolve_write_source(s3_source: str, node_name: str) -> Dict[str, Any]:
    if not S3_SOURCES:
        raise RuntimeError(
            f"{node_name}: No S3 storage is configured. Please set S3_BUCKET_NAME in your .env file."
        )
    return _get_source(s3_source or _default_write_source_name(), node_name)


# SaveVideoToS3 only accepts these local video extensions
ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif", ".webp")

# Optional: used by ReadImageFromS3 when the file is actually a video
FFMPEG_PATH = shutil.which("ffmpeg")


class ReadImageFromS3:
    """
    Read one file from S3 and return it as a ComfyUI IMAGE.

    Flow:
    1. Download from S3
       - s3_source = auto: try each key set in order until one works
       - s3_source = a named key set: only use that one
    2. Try to read it as an image
       - normal image: read it directly
       - animated image / HEIF: use the first frame
    3. If that fails, use ffmpeg and take the first frame
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
            },
            # Optional so old workflows without this input still run as "auto"
            "optional": {
                "s3_source": (
                    [AUTO_SOURCE] + S3_SOURCE_NAMES,
                    {
                        "default": AUTO_SOURCE,
                        "tooltip": "Which S3 key set to read from. 'auto' tries each configured one in order.",
                    },
                ),
            },
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

    def _download_to_temp_file(self, s3_key: str, s3_source: str) -> str:
        if not S3_SOURCES:
            raise RuntimeError(
                "ReadImageFromS3: No S3 storage is configured. Please set S3_BUCKET_NAME in your .env file."
            )

        if not s3_source or s3_source == AUTO_SOURCE:
            sources = S3_SOURCES
        else:
            sources = [_get_source(s3_source, "ReadImageFromS3")]

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=os.path.splitext(s3_key)[1] or ".bin",
        )
        temp_path = temp_file.name
        temp_file.close()

        errors = []
        for source in sources:
            label = source["name"]
            try:
                start_time = time.time()
                source["client"].download_file(source["bucket"], s3_key, temp_path)
                print(
                    f"S3 download completed ({label}): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
                )
                return temp_path
            except Exception as error:
                print(f"S3 download failed ({label}) for '{s3_key}': {error}")
                errors.append(f"{label}: {error}")

        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise RuntimeError(
            f"ReadImageFromS3: Failed to download '{s3_key}' from all configured storage. "
            + " | ".join(errors)
        )

    def load_image(
        self, s3_key: str, s3_source: str = AUTO_SOURCE
    ) -> Tuple[torch.Tensor]:
        s3_key = (s3_key or "").strip()
        temp_path = self._download_to_temp_file(s3_key, s3_source)

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
    def IS_CHANGED(cls, s3_key: str, s3_source: str = AUTO_SOURCE) -> str:
        """
        ComfyUI cache invalidation method.

        Returning the S3 key + source ensures the node re-executes when
        a different S3 object or key set is specified.
        """
        return f"{s3_source}|{s3_key}"

    @classmethod
    def VALIDATE_INPUTS(
        cls, s3_key: str, s3_source: str = AUTO_SOURCE
    ) -> Union[bool, str]:
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
        if s3_source and s3_source != AUTO_SOURCE and s3_source not in S3_SOURCE_NAMES:
            return f"S3 source '{s3_source}' is not configured. Available: {S3_SOURCE_NAMES}"
        return True


def _batch_s3_key(s3_key: str, index: int, total: int) -> str:
    """
    Key for image `index` (0-based) of a batch.

    A single image keeps the key as-is. A batch gets a 1-based, zero-padded
    number before the extension: images/result.png -> images/result_00001.png
    """
    if total == 1:
        return s3_key
    stem, extension = os.path.splitext(s3_key)
    return f"{stem}_{index + 1:05d}{extension}"


def _native_preview(images: torch.Tensor, prompt=None, extra_pnginfo=None):
    """
    Build the UI preview with ComfyUI's own PreviewImage node, so it looks
    and behaves exactly like a Preview Image node. Returns None outside
    ComfyUI or if the preview fails; the S3 upload is not affected.
    """
    try:
        # ComfyUI's top-level `nodes` module (not this file, which is
        # imported as part of this package)
        import nodes as comfy_nodes

        preview_node_class = getattr(comfy_nodes, "PreviewImage", None)
        if preview_node_class is None:
            print("SaveImageToS3: ComfyUI PreviewImage not found, skipping preview")
            return None
        return preview_node_class().save_images(
            images, prompt=prompt, extra_pnginfo=extra_pnginfo
        )["ui"]
    except Exception as error:
        print(f"SaveImageToS3: Preview failed, skipping preview: {error}")
        return None


class SaveImageToS3:
    """
    Save a ComfyUI IMAGE batch to S3 as PNG files, and show a preview.

    - 1 image:  uploaded to s3_key as-is
    - N images: uploaded to <stem>_00001<ext>, <stem>_00002<ext>, ...
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
                "s3_key": (
                    "STRING",
                    {
                        "default": "images/result.png",
                        "multiline": False,
                        "tooltip": "For a batch, a number is added before the extension: result_00001.png, result_00002.png, ...",
                    },
                ),
            },
            "optional": {
                "s3_source": _write_source_input(),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    CATEGORY = "image"
    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_to_s3"
    OUTPUT_NODE = True

    def _upload_one(self, image_tensor: torch.Tensor, source, s3_key: str):
        image_array = image_tensor.detach().cpu().numpy()
        image_array = np.clip(image_array * 255.0, 0, 255).astype(np.uint8)
        pil_image = Image.fromarray(image_array)

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                pil_image.save(temp_file.name, format="PNG")
                temp_file_path = temp_file.name

            start_time = time.time()
            source["client"].upload_file(temp_file_path, source["bucket"], s3_key)
            print(
                f"S3 upload completed (image, {source['name']}): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
            )
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    def save_to_s3(
        self,
        image: torch.Tensor,
        s3_key: str,
        s3_source: str = "",
        prompt=None,
        extra_pnginfo=None,
    ):
        if not isinstance(image, torch.Tensor):
            raise ValueError("SaveImageToS3: 'image' parameter must be a torch.Tensor")

        s3_key = (s3_key or "").strip().lstrip("/")
        if not s3_key:
            raise RuntimeError("SaveImageToS3: s3_key cannot be empty.")

        source = _resolve_write_source(s3_source, "SaveImageToS3")

        images = image if image.ndim == 4 else image[None, ...]
        total = images.shape[0]

        uploaded = []
        for index in range(total):
            key = _batch_s3_key(s3_key, index, total)
            try:
                self._upload_one(images[index], source, key)
            except Exception as error:
                raise RuntimeError(
                    f"SaveImageToS3: Failed to upload to '{key}' ({index + 1}/{total}): {error}. "
                    f"Already uploaded: {uploaded or 'none'}"
                )
            uploaded.append(key)

        ui = _native_preview(images, prompt, extra_pnginfo)
        return {"ui": ui} if ui else ()


def _comfy_folder_paths():
    """ComfyUI's folder_paths module, or None when running outside ComfyUI."""
    try:
        import folder_paths

        return folder_paths
    except ImportError:
        return None


# Native ComfyUI VIDEO inputs can be written as these containers;
# anything else is written as mp4
NATIVE_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm")


def _video_preview(file_path: str):
    """
    Build the same UI preview ComfyUI's Save Video node returns:
    {"images": [{filename, subfolder, type}], "animated": (True,)}

    Files already under ComfyUI's output/temp/input folders are shown in
    place; other files are copied into the temp folder first. Returns None
    outside ComfyUI or if anything fails; the S3 upload is not affected.
    """
    folder_paths = _comfy_folder_paths()
    if folder_paths is None:
        return None

    try:
        path = os.path.abspath(file_path)
        extension = os.path.splitext(path)[1].lower()
        result = None

        for folder_type, base in (
            ("output", folder_paths.get_output_directory()),
            ("temp", folder_paths.get_temp_directory()),
            ("input", folder_paths.get_input_directory()),
        ):
            base = os.path.abspath(base)
            try:
                inside = os.path.commonpath([path, base]) == base
            except ValueError:
                # Different drives on Windows
                inside = False
            if inside:
                subfolder, filename = os.path.split(os.path.relpath(path, base))
                result = {"filename": filename, "subfolder": subfolder, "type": folder_type}
                break

        if result is None:
            temp_dir = folder_paths.get_temp_directory()
            os.makedirs(temp_dir, exist_ok=True)
            filename = f"s3_video_preview_{uuid.uuid4().hex[:12]}{extension}"
            shutil.copy2(path, os.path.join(temp_dir, filename))
            result = {"filename": filename, "subfolder": "", "type": "temp"}

        # The frontend plays "animated" results as <video>, which cannot play
        # a gif; a gif shown as a plain image still animates
        return {"images": [result], "animated": (extension != ".gif",)}
    except Exception as error:
        print(f"SaveVideoToS3: Preview failed, skipping preview: {error}")
        return None


class SaveVideoToS3:
    """
    Upload one video to S3 and show it in the node.

    Connect exactly one of:
    - video:      native ComfyUI VIDEO (Load Video, Create Video, ...)
    - filenames:  VHS_FILENAMES from VideoHelperSuite's Video Combine
    - video_path: absolute local file path (STRING)
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "s3_key": (
                    "STRING",
                    {"default": "videos/result.mp4", "multiline": False},
                ),
            },
            "optional": {
                "video": (
                    "VIDEO",
                    {"tooltip": "Native ComfyUI video (e.g. from Create Video). Saved as mp4/mkv/webm based on the s3_key extension."},
                ),
                "filenames": (
                    "VHS_FILENAMES",
                    {"tooltip": "Filenames output of VideoHelperSuite's Video Combine. The final video file is uploaded."},
                ),
                "video_path": (
                    "STRING",
                    {"forceInput": True, "tooltip": "Absolute local path of a video file."},
                ),
                "s3_source": _write_source_input(),
            },
        }

    CATEGORY = "image"
    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_video_to_s3"
    OUTPUT_NODE = True

    def _check_local_file(self, local_video_path: str) -> str:
        local_video_path = os.path.expanduser(str(local_video_path).strip().strip("\"'"))

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
        return local_video_path

    def _vhs_final_file(self, filenames) -> str:
        # VHS_FILENAMES is (save_output, [files...]); the last file is the
        # final video (the one with audio, if audio was added)
        if not (
            isinstance(filenames, (tuple, list))
            and len(filenames) == 2
            and isinstance(filenames[1], (tuple, list))
        ):
            raise RuntimeError(
                "SaveVideoToS3: 'filenames' is not a VHS_FILENAMES value from Video Combine."
            )
        files = list(filenames[1])
        if not files:
            raise RuntimeError("SaveVideoToS3: Video Combine returned no files.")
        return files[-1]

    def _write_native_video(self, video, s3_key: str) -> Tuple[str, bool]:
        """
        Write a native VIDEO to a local file. Returns (path, keep): keep is
        True when the file lives in ComfyUI's temp folder for the preview.
        """
        extension = os.path.splitext(s3_key)[1].lower()
        if extension not in NATIVE_VIDEO_EXTENSIONS:
            extension = ".mp4"

        folder_paths = _comfy_folder_paths()
        if folder_paths is not None:
            temp_dir = folder_paths.get_temp_directory()
            os.makedirs(temp_dir, exist_ok=True)
            path = os.path.join(temp_dir, f"s3_video_{uuid.uuid4().hex[:12]}{extension}")
            keep = True
        else:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
            path = temp_file.name
            temp_file.close()
            keep = False

        try:
            # The container (mp4/mkv/webm) follows the file extension
            video.save_to(path)
        except Exception as error:
            if os.path.exists(path):
                os.remove(path)
            raise RuntimeError(f"SaveVideoToS3: Failed to write the video: {error}")
        return path, keep

    def save_video_to_s3(
        self,
        s3_key: str,
        video=None,
        filenames=None,
        video_path: str = None,
        s3_source: str = "",
    ):
        source = _resolve_write_source(s3_source, "SaveVideoToS3")

        s3_key = (s3_key or "").strip().lstrip("/")
        if not s3_key:
            raise RuntimeError("SaveVideoToS3: s3_key cannot be empty.")

        has_path = video_path is not None and str(video_path).strip() != ""
        connected = [
            name
            for name, present in (
                ("video", video is not None),
                ("filenames", filenames is not None),
                ("video_path", has_path),
            )
            if present
        ]
        if len(connected) != 1:
            raise RuntimeError(
                "SaveVideoToS3: Connect exactly one of 'video', 'filenames' or 'video_path'"
                + (f" (got: {', '.join(connected)})." if connected else ".")
            )

        local_video_path = None
        delete_after = False
        if video is not None:
            local_video_path, keep = self._write_native_video(video, s3_key)
            delete_after = not keep
        elif filenames is not None:
            local_video_path = self._check_local_file(self._vhs_final_file(filenames))
        else:
            local_video_path = self._check_local_file(video_path)

        try:
            start_time = time.time()
            source["client"].upload_file(local_video_path, source["bucket"], s3_key)
            print(
                f"S3 upload completed (video, {source['name']}): s3_key='{s3_key}', duration={time.time() - start_time:.3f}s"
            )
        except Exception as error:
            raise RuntimeError(
                f"SaveVideoToS3: Failed to upload '{local_video_path}' to '{s3_key}': {error}"
            )
        finally:
            if delete_after and os.path.exists(local_video_path):
                os.remove(local_video_path)

        ui = None if delete_after else _video_preview(local_video_path)
        return {"ui": ui} if ui else ()


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
