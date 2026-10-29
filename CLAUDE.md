# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a ComfyUI custom node extension that provides AWS S3 integration for image processing workflows. The project enables users to read images directly from S3 storage and save processed images back to S3, eliminating the need for manual file transfers.

## Dependencies Installation

The project requires the following Python dependencies:
```bash
pip install boto3 torch pillow python-dotenv numpy
```

## Configuration

### Environment Setup
1. Create a `.env` file in the root directory (add `S3_2_*`, `S3_3_*`, ... for more key sets):
```env
S3_NAME=aws
S3_ACCESS_KEY_ID=your_access_key_here
S3_SECRET_ACCESS_KEY=your_secret_key_here
S3_REGION=us-east-1
S3_BUCKET=your-bucket-name
```
The old `AWS_ACCESS_KEY_ID` / `S3_BUCKET_NAME` / `FALLBACK_*` variables still work.

2. Set up S3 bucket structure (flexible - users can now specify custom paths):
```
your-bucket-name/
├── any-folder/          # Images can be in any folder structure
│   └── subfolder/
└── output/              # Default output folder (still used by SaveImageToS3)
```

Note: With the updated ReadImageFromS3 node, users can specify any S3 object key directly in the ComfyUI interface, so the folder structure is now flexible.

## Code Architecture

### Main Components

- **`__init__.py`**: Simple module initialization that exports NODE_CLASS_MAPPINGS
- **`nodes.py`**: Core implementation containing two main node classes:
  - `ReadImageFromS3`: Loads images from S3 using custom object keys (user-specified paths)
  - `SaveImageToS3`: Saves processed image tensors to S3 output folder

### Key Architecture Details

1. **Image Processing Pipeline**:
   - Images are downloaded to temporary files for PIL processing
   - EXIF orientation correction is automatically applied
   - All images are converted to RGB format (alpha channels discarded)
   - Tensors are normalized to [0, 1] range for ComfyUI compatibility

2. **S3 Integration**:
   - Uses boto3 clients built from key sets in env: first set `S3_NAME`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_BUCKET`, `S3_ENDPOINT_URL`; extra sets `S3_2_*`, `S3_3_*`, ...
   - Legacy env (`AWS_*`/`S3_BUCKET_NAME` as "primary", `FALLBACK_*`, `FALLBACK_2_*`, ...) still loads, before the numbered sets
   - Nodes expose an optional `s3_source` dropdown with the key set names (read: `auto` tries all in order; save: defaults to the first set)
   - Supports standard image formats: .png, .jpg, .jpeg, .bmp, .webp, .tiff
   - Custom S3 object key input (users can specify any path within the bucket)
   - S3 object existence validation before processing
   - Automatic timestamp generation for saved files

3. **ComfyUI Node Structure**:
   - Both nodes follow ComfyUI conventions with INPUT_TYPES, RETURN_TYPES, etc.
   - Category: "image" 
   - Input validation and cache invalidation methods implemented
   - Error handling for S3 operations and image processing

### AWS IAM Permissions Required

The AWS credentials need these S3 permissions:
- `s3:ListBucket` - List available images
- `s3:GetObject` - Download images from input folder  
- `s3:PutObject` - Upload processed images to output folder

## Installation in ComfyUI

1. Clone repository to ComfyUI's custom_nodes directory:
```bash
cd /ComfyUI/custom_nodes
git clone https://github.com/lakkiy/ComfyUI-RWImageS3.git
```

2. Install dependencies and restart ComfyUI
3. Nodes will appear in the "image" category

## Development Notes

- No build, test, or lint commands are configured in this project
- The codebase is purely Python with no additional tooling setup
- Temporary files are properly cleaned up after processing
- Error handling includes network failures and image processing errors
- The code follows defensive programming practices with proper exception handling