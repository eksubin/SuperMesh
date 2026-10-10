import os
from PIL import Image, ImageDraw

def create_2kb_image(output_path="test_2kb.png"):
    # 1. Create a 220x220 canvas with a dark gray background
    # (RGB color format gives enough byte depth to hit 2KB at this resolution)
    width, height = 220, 220
    image = Image.new("RGB", (width, height), color=(40, 44, 52))
    
    # 2. Draw a smooth colored circle in the center
    draw = ImageDraw.Draw(image)
    left_up = (40, 40)
    right_down = (180, 180)
    draw.ellipse([left_up, right_down], fill=(78, 164, 219))
    
    # 3. Save the image as a standard PNG
    image.save(output_path, format="PNG")
    
    # 4. Check and display the actual file size
    file_size_bytes = os.path.getsize(output_path)
    file_size_kb = file_size_bytes / 1024
    
    print(f"🎉 Success! Image created at: {output_path}")
    print(f"📊 File size: {file_size_bytes} bytes ({file_size_kb:.2f} KB)")

if __name__ == "__main__":
    create_2kb_image()
