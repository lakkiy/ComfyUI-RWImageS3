# ComfyUI-RWImageS3

ComfyUI custom nodes for reading images from S3 and saving images/videos to S3.

## Features

- Read an image directly from S3 using an explicit `s3_key`
- Automatically fall back to the first frame when the downloaded object is actually a video/live photo
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

3. Ensure `ffmpeg` is available in `PATH` if you want `Read Image From S3` to support:
   - files whose extension says `.png/.jpg` but whose actual contents are video
   - Apple Live Photo companion videos (`.mov`)
   - other video inputs where only the first frame should be used

4. Configure `.env`:

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

5. Restart ComfyUI.

## Nodes

### Read Image From S3

Category: `image`

Input:
- `s3_key` (STRING): Full object key in bucket, e.g. `images/cat.png`

Output:
- `IMAGE`

Behavior:
- Downloads media from primary storage; if configured and primary fails, tries fallback storage
- First tries normal still-image decoding with Pillow
- If Pillow cannot decode the file, automatically uses `ffmpeg` to extract the first frame
- This means it can handle:
  - normal images
  - files mislabeled as `.png/.jpg` in S3 but actually containing video
  - Apple Live Photo video files such as `.mov`
- Converts output to RGB tensor in `[0, 1]`

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

## Supported inputs for Read Image From S3

### Direct still-image decode

- `.png`
- `.jpg`
- `.jpeg`
- `.bmp`
- `.webp`
- `.tiff`

### First-frame fallback via `ffmpeg`

- `.mp4`
- `.mov`
- `.webm`
- `.mkv`
- `.avi`
- mislabeled files where the extension is image-like but the file contents are actually video

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

- `ffmpeg is required to decode video/live photo inputs, but it was not found in PATH`
  - Install `ffmpeg` and make sure the ComfyUI process can find it.

## Version

Current version: `3.1.0`
