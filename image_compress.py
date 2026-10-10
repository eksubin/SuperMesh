import os
import struct
import torch
from compressai.zoo import bmshj2018_factorized
from PIL import Image
from torchvision.transforms import ToTensor, ToPILImage

device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"Utilizing compute device: {device.upper()}")

model = bmshj2018_factorized(quality=1, pretrained=True).to(device).eval()

input_path = "test_2kb.png"
binary_output_path = "compressed_output.bits"
recovered_output_path = "recovered_neural_image.png"

img = Image.open(input_path).convert("RGB")
x = ToTensor()(img).unsqueeze(0).to(device)

# ==========================================
# PHASE 1: COMPRESS & WRITE BINARY
# ==========================================
with torch.no_grad():
    compressed_data = model.compress(x)

strings = compressed_data["strings"]
shape = compressed_data["shape"]

with open(binary_output_path, "wb") as f:
    # Save shape
    f.write(struct.pack("ii", shape[0], shape[1]))

    # Save nested string structure
    f.write(struct.pack("i", len(strings)))

    for string_group in strings:
        f.write(struct.pack("i", len(string_group)))

        for byte_string in string_group:
            f.write(struct.pack("i", len(byte_string)))
            f.write(byte_string)

original_size = os.path.getsize(input_path)
compressed_size = os.path.getsize(binary_output_path)

print("Neural Compression Complete!")
print(f"Original Size:        {original_size} bytes ({original_size / 1024:.2f} KB)")
print(f"Raw Compressed Size: {compressed_size} bytes ({compressed_size / 1024:.2f} KB)")

# ==========================================
# PHASE 2: READ BINARY & DECOMPRESS
# ==========================================
with open(binary_output_path, "rb") as f:
    h, w = struct.unpack("ii", f.read(8))
    recovered_shape = (h, w)

    num_groups = struct.unpack("i", f.read(4))[0]

    recovered_strings = []

    for _ in range(num_groups):
        group_len = struct.unpack("i", f.read(4))[0]
        string_group = []

        for _ in range(group_len):
            str_len = struct.unpack("i", f.read(4))[0]
            raw_bytes = f.read(str_len)
            string_group.append(raw_bytes)

        recovered_strings.append(string_group)

with torch.no_grad():
    reconstructed = model.decompress(recovered_strings, recovered_shape)

output_tensor = reconstructed["x_hat"].squeeze(0).clamp(0, 1).cpu()
output_img = ToPILImage()(output_tensor)
output_img.save(recovered_output_path)

print("Decompression Success!")
print(f"Recovered image saved to: {recovered_output_path}")