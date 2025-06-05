# ComfyUI S3 Image Nodes

A powerful ComfyUI extension that enables seamless integration with AWS S3 storage for reading and saving images directly within your ComfyUI workflows. This extension eliminates the need for manual file transfers by providing direct S3 connectivity for your image processing pipelines.

## 🚀 What This Project Does

This ComfyUI extension provides two custom nodes that enable direct interaction with AWS S3 storage:

- **Read images directly from S3** into your ComfyUI workflows
- **Save processed images directly to S3** with automatic timestamping
- **Dynamic dropdown population** showing available images in your S3 bucket
- **Automatic image processing** including EXIF orientation correction and format standardization

### 📸 Supported Image Formats

The extension supports the following single-frame image formats:
- `.png` - Portable Network Graphics
- `.jpg` / `.jpeg` - JPEG images  
- `.bmp` - Bitmap images
- `.webp` - WebP images
- `.tiff` - Tagged Image File Format

> **⚠️ Important Note**: This extension processes images as **RGB only** and does not support alpha channels or masks. All images are converted to 3-channel RGB format during processing.

## 📦 Installation

### 1. Clone the Repository

Navigate to your ComfyUI custom nodes directory and clone this repository:

```bash
# Navigate to ComfyUI custom nodes directory
cd /ComfyUI/custom_nodes

# Clone the repository
git clone https://github.com/Jayanth-y/ComfyUI-Nodes-for-AWS-S3-Image-Connect.git

# Navigate into the project directory
cd comfyui-s3-connect
```

### 2. Install Dependencies

Install the required Python packages using pip. If you're using a virtual environment, make sure to activate it first:

```bash
# If using virtual environment, activate it first
# source venv/bin/activate  # On Linux/Mac
# venv\Scripts\activate     # On Windows

# Install all required dependencies
pip install boto3 torch pillow python-dotenv numpy
```

### 3. Configure AWS Credentials

Edit the `.env` file with your AWS configuration:

```env
# AWS credentials
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name
```

### 4. Set Up S3 Bucket Structure

Create the following folder structure in your S3 bucket:
```
your-bucket-name/
├── input/     # Upload your source images here
└── output/    # Processed images will be saved here
```

### 5. Restart ComfyUI

Restart ComfyUI to load the new custom nodes. The nodes will appear in the `image` category.

## 🧩 Available Nodes

### 1. Read Image From S3

**Category**: `image`  
**Node Name**: `Read Image From S3`

This node loads images directly from your S3 bucket's input folder into ComfyUI workflows.

#### Features:
- **Dynamic file listing**: Automatically populates dropdown with available images from S3
- **EXIF orientation correction**: Automatically rotates images based on EXIF data
- **Format standardization**: Converts all images to RGB format for consistent processing
- **Tensor normalization**: Outputs properly normalized tensors in [0, 1] range

#### Inputs:
- **image** (dropdown): Select from available images in your S3 input folder

#### Outputs:
- **IMAGE**: PyTorch tensor of shape `[1, H, W, 3]` with RGB values normalized to [0, 1]

#### Usage:
1. Upload images to your S3 bucket's `input/` folder
2. Add the "Read Image From S3" node to your workflow
3. Select the desired image from the dropdown
4. Connect the output to other image processing nodes

---

### 2. Save Image To S3

**Category**: `image`  
**Node Name**: `Save Image To S3`

This node saves processed images from ComfyUI workflows directly to your S3 bucket's output folder.

#### Features:
- **Automatic timestamping**: Appends timestamp to filenames for uniqueness
- **PNG format**: Saves images in lossless PNG format
- **Pass-through output**: Returns the original image tensor for downstream nodes
- **S3 key tracking**: Provides the S3 location of saved files
- **Batch handling**: Supports both batched `[1, H, W, 3]` and unbatched `[H, W, 3]` tensors

#### Inputs:
- **image** (IMAGE): PyTorch tensor containing the image data to save
- **filename_prefix** (STRING): Base name for the saved file (default: "output_image")

#### Outputs:
- **IMAGE**: The same input tensor (unchanged, for connecting to preview nodes)
- **STRING**: S3 key of the uploaded file (e.g., "output/prefix_20240604123456.png")

#### Usage:
1. Connect an image tensor from any image processing node
2. Set a descriptive filename prefix
3. The node will save the image to S3 and return both the image and S3 location
4. Connect the image output to PreviewImage or other nodes as needed

## 🔧 Configuration Details

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | Your AWS access key ID | `AKIAIOSFODNN7EXAMPLE` |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret access key | `wJalrXUtnFEMI/K7MDENG/...` |
| `AWS_REGION` | AWS region for your S3 bucket | `us-east-1` |
| `S3_BUCKET_NAME` | Name of your S3 bucket | `my-comfyui-bucket` |

### S3 Permissions Required

Your AWS credentials need the following S3 permissions:
- `s3:ListBucket` - To list available images
- `s3:GetObject` - To download images from input folder
- `s3:PutObject` - To upload processed images to output folder

Example IAM policy:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket"
            ],
            "Resource": "arn:aws:s3:::your-bucket-name"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::your-bucket-name/*"
        }
    ]
}
```

## 🎯 Example Workflow

Here's a typical workflow using these S3 nodes:

1. **Upload source images** to your S3 `input/` folder
2. **Add "Read Image From S3"** node and select your image
3. **Apply image processing** nodes (upscaling, filtering, etc.)
4. **Add "Save Image To S3"** node to save the result
5. **Optional**: Connect to PreviewImage to see the result in ComfyUI
6. **Check your S3 `output/` folder** for the processed images

## 🐛 Troubleshooting

### Common Issues:

**"Image not found in S3 input folder"**
- Verify your image is uploaded to the correct S3 bucket and input folder
- Check that the image has a supported file extension
- Ensure your AWS credentials have `s3:ListBucket` permission

**"Failed to download from S3"**
- Verify your AWS credentials are correct
- Check that your AWS region matches your S3 bucket's region
- Ensure your credentials have `s3:GetObject` permission

**"Failed to upload to S3"**
- Verify your credentials have `s3:PutObject` permission
- Check that your S3 bucket exists and is accessible
- Ensure the output folder exists in your bucket

**Empty dropdown in Read Image node**
- Upload supported image files to your S3 input folder
- Verify your `.env` configuration is correct
- Check ComfyUI console for error messages

## 📋 Requirements

- **ComfyUI** (latest version recommended)
- **Python 3.8+**
- **AWS Account** with S3 access
- **Dependencies**: boto3, torch, pillow, python-dotenv, numpy

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## 🙋‍♂️ Support

If you encounter any issues or have questions, please:
1. Check the troubleshooting section above
2. Review the ComfyUI console for error messages
3. Open an issue on GitHub with detailed information about your problem

---

**Made with ❤️ for the ComfyUI community**
