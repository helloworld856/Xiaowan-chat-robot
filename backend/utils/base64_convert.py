import base64

def get_image_as_base64(image_path):
    """读取图片转 base64"""
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")
    except Exception:
        return ""