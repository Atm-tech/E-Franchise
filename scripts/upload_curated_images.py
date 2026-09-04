"""Upload the six curated catalogue test images to R2 as compact WebP files."""

from __future__ import annotations

import io
import json
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps
import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import DB_PATH, get_conn, r2_client, r2_config  # noqa: E402


IMAGES = {
    "8906008819502": "https://www.unitedtraderssupermart.com/images/website/product/thumb/fdc240239000b5eb3d56f818527f6b54709cf98e.webp",
    "8901262030151": "https://ktmbazaar.com.au/cdn/shop/files/Amul_ghee_1l.webp?v=1757846590&width=1024",
    "23": "https://www.city.shibetsu.lg.jp/material/images/group/1/satou4.jpg",
    "8901030877148": "https://www.gorevizon.com/wp-content/uploads/2021/03/61wPBqtnxL._SL1000_.jpg",
    "8719514263161": "https://sl-dev-s3.s3.amazonaws.com/product/3c1566ee5c1279ccd9319540e3b6e6d3-1753161056084.webp",
    "A23239020": "https://static-01.daraz.com.np/p/0def7b94e746e55f7038aa877bba55b9.png",
}


def compressed_webp(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 Aapoorti-Catalogue-Test/1.0"})
    with urlopen(request, timeout=30, context=ssl.create_default_context(cafile=certifi.where())) as response:
        source = response.read(8 * 1024 * 1024)
    with Image.open(io.BytesIO(source)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((900, 900), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "WEBP", quality=76, method=6)
        return output.getvalue()


def main() -> None:
    config, missing = r2_config()
    if not config:
        raise SystemExit(f"R2 configuration missing: {', '.join(missing)}")
    client = r2_client(config)
    uploaded: list[tuple[str, str, int]] = []
    with get_conn() as conn:
        for sku, source_url in IMAGES.items():
            body = compressed_webp(source_url)
            safe_sku = sku.lower()
            object_key = f"{config['object_prefix']}/products/catalog-{safe_sku}.webp"
            client.put_object(
                Bucket=config["bucket_name"], Key=object_key, Body=body,
                ContentType="image/webp", CacheControl="public, max-age=31536000, immutable",
            )
            image_url = (
                f"{config['public_base_url']}/{object_key}"
                if config["public_base_url"] else f"/media/r2/{object_key}"
            )
            row = conn.execute("SELECT attributes_json FROM products WHERE sku_code=?", (sku,)).fetchone()
            attributes = json.loads(row["attributes_json"] or "{}") if row else {}
            attributes["test_image_source"] = source_url
            conn.execute(
                "UPDATE products SET image_url=?, attributes_json=? WHERE sku_code=?",
                (image_url, json.dumps(attributes, separators=(",", ":")), sku),
            )
            uploaded.append((sku, object_key, len(body)))
        conn.commit()
    print(f"Uploaded {len(uploaded)} WebP images to R2; database: {DB_PATH}")
    for sku, key, size in uploaded:
        print(f"{sku}: {key} ({size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
