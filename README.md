# ComfyUI-RWImageS3

ComfyUI custom nodes for reading images from S3 and saving images/videos to S3.

## Features

- Read an image directly from S3 using an explicit `s3_key`
- Support HEIC/HEIF uploads when `pillow-heif` is installed, even if the S3 key is mislabeled as `.png/.jpg`
- Automatically fall back to the first frame when the downloaded object is actually a video/live photo
- Save an IMAGE tensor to S3 using an explicit `s3_key`
- Save a local video file to S3 using an explicit `video_path` and `s3_key`
- Multiple named S3 key sets: pick one per node in ComfyUI, or let reads try them all in order
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

`requirements.txt` now includes `pillow-heif`, so `Read Image From S3` can support HEIC/HEIF uploads whose S3 key may still look like `.png/.jpg`.

3. Ensure `ffmpeg` is available in `PATH` if you want `Read Image From S3` to support:
   - files whose extension says `.png/.jpg` but whose actual contents are video
   - Apple Live Photo companion videos (`.mov`)
   - other video inputs where only the first frame should be used

4. Edit `.env` (it ships as a template). One key set is enough:

```env
S3_NAME=aws
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
S3_REGION=us-east-1
S3_BUCKET=your-bucket
# S3_ENDPOINT_URL=https://your-endpoint   # optional, for R2 / MinIO / ...
```

For more key sets, add numbered ones starting at 2:

```env
S3_2_NAME=r2
S3_2_ACCESS_KEY_ID=...
S3_2_SECRET_ACCESS_KEY=...
S3_2_BUCKET=your-r2-bucket
S3_2_ENDPOINT_URL=https://your-account.r2.cloudflarestorage.com

# S3_3_*, S3_4_*, ... add as many as you need
```

- `BUCKET` is required; a set without it is ignored. `REGION` (default `us-east-1`) and `ENDPOINT_URL` are optional.
- `NAME` is what shows up in the node's `s3_source` dropdown (default `default` for the first set, `s3_<N>` for the others). Names must be unique; duplicates get a ` (2)` suffix.
- `auto` tries the first set, then `S3_2`, `S3_3`, ... Gaps are fine (`S3_2`, `S3_4`).

Old-style variables still work, so existing `.env` files need no change. They are loaded before the numbered sets:

| Old variables | Name in dropdown |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`, `ENDPOINT_URL` | `primary` |
| `FALLBACK_AWS_ACCESS_KEY_ID`, ..., `FALLBACK_S3_BUCKET_NAME`, `FALLBACK_ENDPOINT_URL` | `fallback 1` |
| `FALLBACK_2_*`, `FALLBACK_3_*`, ... | `fallback 2`, `fallback 3`, ... |

5. Restart ComfyUI.

## Nodes

### Read Image From S3

Category: `image`

Input:
- `s3_key` (STRING): Full object key in bucket, e.g. `images/cat.png`
- `s3_source` (optional dropdown): `auto (try in order)` (default) or one key set name

Output:
- `IMAGE`

Behavior:
- `auto`: tries each key set in order until one succeeds (same as the old primary → fallback behavior)
- A named key set: downloads only from that one, nothing else is tried
- First tries image decoding with Pillow
- If `pillow-heif` is installed, this also covers HEIC/HEIF files
- If the image is animated, it uses the first frame
- If Pillow cannot decode the file, it automatically uses `ffmpeg` to extract the first frame
- This means it can handle:
  - normal images
  - animated images
  - HEIC/HEIF uploads
  - files mislabeled as `.png/.jpg` in S3 but actually containing video
  - Apple Live Photo video files such as `.mov`
- Converts output to RGB tensor in `[0, 1]`

### Save Image To S3

Category: `image`

Input:
- `image` (IMAGE)
- `s3_key` (STRING): Full target object key, e.g. `results/run1/frame_0001.png`
- `s3_source` (optional dropdown): which key set to upload with (default: the first one)

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
- `s3_source` (optional dropdown): which key set to upload with (default: the first one)

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

### Direct image decode with Pillow

- `.png`
- `.jpg`
- `.jpeg`
- `.bmp`
- `.webp`
- `.tiff`

### HEIC / HEIF image decode with Pillow + `pillow-heif`

- `.heic`
- `.heif`
- mislabeled files where the extension is image-like but the file contents are actually HEIC/HEIF
- if the HEIC/HEIF file is animated, the first frame is used

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

- `No S3 storage is configured`
  - Set at least one key set (e.g. `S3_BUCKET`) in `.env` and restart ComfyUI.

- `S3 source '...' is not configured`
  - The workflow picked a key set name that is not in `.env` (renamed or removed). Pick another one in the dropdown.

- `s3_key cannot be empty`
  - Provide a full object key including folder and filename.

- `Please provide an absolute local video path`
  - Pass an absolute path in `video_path`.

- `Local video file not found`
  - Confirm the file exists on disk and ComfyUI has access.

- `ffmpeg is required to decode video/live photo inputs, but it was not found in PATH`
  - Install `ffmpeg` and make sure the ComfyUI process can find it.

- HEIC/HEIF files still cannot be read
  - Install `pillow-heif` in the same Python environment as ComfyUI.
  - If the file is not really HEIC/HEIF but a video-like container, keep `ffmpeg` installed as fallback.

## Version

Current version: `3.2.0`
