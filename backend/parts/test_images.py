"""LP-BE-401: part images (G§6.3): jpg/png/webp up to 5 MB, brand-specific first."""

import io
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from PIL import Image

from parts.models import PartImage
from parts.services import image_urls

MEDIA = tempfile.mkdtemp(prefix="lp-test-media-")


def png(name="a.png"):
    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA, MEDIA_URL="/media/")
class PartImageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_validation(self):
        PartImage(part_number="P-1", image=png()).full_clean()
        with self.assertRaises(ValidationError) as ctx:
            PartImage(part_number="P-1", image=png("a.gif")).full_clean()
        self.assertIn("image", ctx.exception.message_dict)
        big = SimpleUploadedFile("big.jpg", b"0" * (6 * 1024 * 1024), content_type="image/jpeg")
        with self.assertRaises(ValidationError) as ctx:
            PartImage(part_number="P-1", image=big).full_clean()
        self.assertIn("image", ctx.exception.message_dict)

    def test_part_number_is_normalized(self):
        self.assertEqual(PartImage.objects.create(part_number="17940-25 020", image=png()).part_number, "1794025020")

    def test_brand_specific_first_then_generic_absolute_urls(self):
        generic = PartImage.objects.create(part_number="17940-25020", image=png("g.png"), sort_order=0)
        toyota_2 = PartImage.objects.create(part_number="1794025020", brand="Toyota", image=png("t2.png"), sort_order=2)
        toyota_1 = PartImage.objects.create(part_number="1794025020", brand="TOYOTA", image=png("t1.png"), sort_order=1)
        PartImage.objects.create(part_number="1794025020", brand="Denso", image=png("d.png"))
        PartImage.objects.create(part_number="OTHER", image=png("o.png"))
        request = RequestFactory().get("/", HTTP_HOST="localhost")
        urls = image_urls(request, "17940 25020", "toyota")
        self.assertEqual(urls, [f"http://localhost{img.image.url}" for img in (toyota_1, toyota_2, generic)])
        self.assertEqual(image_urls(request, "1794025020", ""), [f"http://localhost{generic.image.url}"])
