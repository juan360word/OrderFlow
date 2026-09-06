"""Product picture upload, storage and delivery."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.modules.products.images import API_PREFIX, detect_image_type

pytestmark = pytest.mark.api

PRODUCTS = "/api/v1/products"

# A real 1x1 PNG. Real bytes matter here: the whole point of the feature is
# that the server decides the format from the content, so a placeholder string
# would test nothing.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
GIF = b"GIF89a" + b"\x00" * 20
JPEG = b"\xff\xd8\xff" + b"\x00" * 20
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 12


def _upload(data: bytes, name: str = "photo.png", content_type: str = "image/png") -> dict:
    return {"file": (name, data, content_type)}


class TestDetectImageType:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (PNG, "image/png"),
            (GIF, "image/gif"),
            (JPEG, "image/jpeg"),
            (WEBP, "image/webp"),
        ],
    )
    def test_it_recognises_supported_formats(self, data: bytes, expected: str) -> None:
        assert detect_image_type(data) == expected

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"just text",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            b"%PDF-1.7",
            b"RIFF\x00\x00\x00\x00WAVE",  # a RIFF container that is not WebP
        ],
    )
    def test_it_rejects_everything_else(self, data: bytes) -> None:
        assert detect_image_type(data) is None


class TestUpload:
    async def test_an_admin_can_attach_a_picture(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-001")

        response = await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        assert response.status_code == 200
        assert response.json()["image_url"] == f"{PRODUCTS}/{product.id}/image"

    async def test_a_product_without_a_picture_reports_none(
        self, client: AsyncClient, make_product: Callable[..., object]
    ) -> None:
        product = await make_product(sku="IMG-002")

        response = await client.get(f"{PRODUCTS}/{product.id}")

        assert response.json()["image_url"] is None

    async def test_the_content_type_comes_from_the_bytes_not_the_client(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """A client may label anything however it likes. It is not believed."""
        product = await make_product(sku="IMG-003")

        await client.post(
            f"{PRODUCTS}/{product.id}/image",
            headers=admin_headers,
            # GIF bytes, announced as a PNG.
            files=_upload(GIF, name="lie.png", content_type="image/png"),
        )
        served = await client.get(f"{PRODUCTS}/{product.id}/image")

        assert served.headers["content-type"] == "image/gif"

    async def test_a_file_that_is_not_an_image_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """The attack this blocks: serving executable content from our origin."""
        product = await make_product(sku="IMG-004")

        response = await client.post(
            f"{PRODUCTS}/{product.id}/image",
            headers=admin_headers,
            files=_upload(b"#!/bin/sh\necho pwned\n", name="evil.png"),
        )

        assert response.status_code == 422

    async def test_an_svg_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """SVG is a document that can carry script, not a picture format."""
        product = await make_product(sku="IMG-005")

        response = await client.post(
            f"{PRODUCTS}/{product.id}/image",
            headers=admin_headers,
            files=_upload(
                b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                name="x.svg",
                content_type="image/svg+xml",
            ),
        )

        assert response.status_code == 422

    async def test_an_empty_file_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-006")

        response = await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(b"")
        )

        assert response.status_code == 422

    async def test_uploading_twice_replaces_rather_than_accumulates(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        product = await make_product(sku="IMG-007")
        url = f"{PRODUCTS}/{product.id}/image"

        await client.post(url, headers=admin_headers, files=_upload(PNG))
        await client.post(url, headers=admin_headers, files=_upload(GIF, name="second.gif"))

        rows = (await session.execute(text("SELECT count(*) FROM product_images"))).scalar_one()
        assert rows == 1
        assert (await client.get(url)).headers["content-type"] == "image/gif"

    async def test_it_is_stored_byte_for_byte(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-008")

        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )
        served = await client.get(f"{PRODUCTS}/{product.id}/image")

        assert served.content == PNG

    async def test_uploading_to_a_product_that_does_not_exist_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{PRODUCTS}/{uuid.uuid4()}/image", headers=admin_headers, files=_upload(PNG)
        )

        assert response.status_code == 404


class TestAuthorization:
    async def test_a_customer_cannot_upload(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-010")

        response = await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=auth_headers, files=_upload(PNG)
        )

        assert response.status_code == 403

    async def test_an_anonymous_caller_cannot_upload(
        self, client: AsyncClient, make_product: Callable[..., object]
    ) -> None:
        product = await make_product(sku="IMG-011")

        response = await client.post(f"{PRODUCTS}/{product.id}/image", files=_upload(PNG))

        assert response.status_code == 401

    async def test_anyone_may_read_a_picture(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """An <img> tag cannot send an Authorization header."""
        product = await make_product(sku="IMG-012")
        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        assert (await client.get(f"{PRODUCTS}/{product.id}/image")).status_code == 200

    async def test_a_customer_cannot_delete(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-013")
        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        response = await client.delete(f"{PRODUCTS}/{product.id}/image", headers=auth_headers)

        assert response.status_code == 403


class TestDelete:
    async def test_removing_a_picture_clears_the_url(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-020")
        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        assert (
            await client.delete(f"{PRODUCTS}/{product.id}/image", headers=admin_headers)
        ).status_code == 204
        assert (await client.get(f"{PRODUCTS}/{product.id}")).json()["image_url"] is None
        assert (await client.get(f"{PRODUCTS}/{product.id}/image")).status_code == 404

    async def test_removing_a_picture_that_is_not_there_is_404(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="IMG-021")

        response = await client.delete(f"{PRODUCTS}/{product.id}/image", headers=admin_headers)

        assert response.status_code == 404


class TestCacheCoherence:
    async def test_a_cached_product_does_not_keep_saying_it_has_no_picture(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """The read is cached for minutes; the upload must drop that copy."""
        product = await make_product(sku="IMG-030")
        assert (await client.get(f"{PRODUCTS}/{product.id}")).json()["image_url"] is None

        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        assert (await client.get(f"{PRODUCTS}/{product.id}")).json()["image_url"] is not None


class TestStorageLayout:
    async def test_deleting_a_product_takes_its_picture_with_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """The foreign key cascades, so no orphan bytes are left behind."""
        product = await make_product(sku="IMG-040")
        await client.post(
            f"{PRODUCTS}/{product.id}/image", headers=admin_headers, files=_upload(PNG)
        )

        await session.execute(text("DELETE FROM products WHERE id = :pid"), {"pid": product.id})
        await session.commit()

        remaining = (
            await session.execute(text("SELECT count(*) FROM product_images"))
        ).scalar_one()
        assert remaining == 0

    def test_the_url_prefix_matches_the_configured_api_prefix(self, settings: Settings) -> None:
        """Guards the one constant that is duplicated rather than imported."""
        assert settings.api_v1_prefix == API_PREFIX
