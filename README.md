# ComfyUI-RWImageS3

ComfyUI custom nodes for reading images from S3 and saving images/videos to S3.

## Features

- Read an image directly from S3 using an explicit `s3_key`
- Save an IMAGE tensor to S3 using an explicit `s3_key`
- Save a local video file to S3 using an explicit `video_path` and `s3_key`
- Optional fallback S3 storage for image reads
- Optional custom S3 endpoint support (R2, MinIO, etc.)
- Upload/download timing logs in ComfyUI console

## Installation

1. Clone into ComfyUI custom nodes:

```bash
cd /ComfyUI/custom_nodes
git clone https://github.com/lakkiy/ComfyUI-RWImageS3.git
cd ComfyUI-RWImageS3
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure `.env`:

```env
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket

# Optional: custom S3-compatible endpoint
# ENDPOINT_URL=https://your-endpoint

# Optional fallback storage for reads
# FALLBACK_AWS_ACCESS_KEY_ID=...
# FALLBACK_AWS_SECRET_ACCESS_KEY=...
# FALLBACK_AWS_REGION=us-east-1
# FALLBACK_S3_BUCKET_NAME=...
# FALLBACK_ENDPOINT_URL=https://your-fallback-endpoint
```

4. Restart ComfyUI.

## Nodes

### Read Image From S3

Category: `image`

Input:
- `s3_key` (STRING): Full object key in bucket, e.g. `images/cat.png`

Output:
- `IMAGE`

Behavior:
- Downloads image from primary storage; if configured and primary fails, tries fallback storage
- Applies EXIF transpose
- Converts image to RGB tensor in `[0, 1]`

### Save Image To S3

Category: `image`

Input:
- `image` (IMAGE)
- `s3_key` (STRING): Full target object key, e.g. `results/run1/frame_0001.png`

Output:
- No outputs (`OUTPUT_NODE = True`)

Behavior:
- Converts tensor to PNG and uploads to S3
- Prints upload completion log with duration

### Save Video To S3

Category: `image`

Input:
- `video_path` (STRING, force input): Absolute local path, e.g. `/root/autodl-tmp/ComfyUI/output/clip.mp4`
- `s3_key` (STRING): Full target object key, e.g. `results/run1/clip.mp4`

Output:
- No outputs (`OUTPUT_NODE = True`)

Behavior:
- Validates absolute path and file existence
- Validates extension: `.mp4 .mov .webm .mkv .avi .gif`
- Uploads file to S3 and prints upload duration

### Is Mask Empty

Category: `image`

Input:
- `mask` (MASK)

Output:
- `BOOLEAN`

## Supported image formats (read)

- `.png`
- `.jpg`
- `.jpeg`
- `.bmp`
- `.webp`
- `.tiff`

## Required IAM permissions

- `s3:GetObject`
- `s3:PutObject`
- `s3:ListBucket` (recommended)

## Troubleshooting

- `S3_BUCKET_NAME is not configured`
  - Set `S3_BUCKET_NAME` in `.env` and restart ComfyUI.

- `s3_key cannot be empty`
  - Provide a full object key including folder and filename.

- `Please provide an absolute local video path`
  - Pass an absolute path in `video_path`.

- `Local video file not found`
  - Confirm the file exists on disk and ComfyUI has access.

## Version

Current version: `3.0.0`
