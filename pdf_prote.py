from pathlib import Path
from io import BytesIO

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import pikepdf


def create_watermark(text, output_path):
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)

    width, height = A4

    c.setFont("Helvetica", 40)
    c.setFillGray(0.75)

    c.translate(width / 2, height / 2)
    c.rotate(45)

    c.drawCentredString(0, 0, text)

    c.save()
    packet.seek(0)

    with open(output_path, "wb") as f:
        f.write(packet.read())


def add_watermark(input_pdf, output_pdf, watermark_text):
    temp_watermark = "temp_watermark.pdf"

    create_watermark(watermark_text, temp_watermark)

    reader = PdfReader(input_pdf)
    watermark = PdfReader(temp_watermark)
    writer = PdfWriter()

    watermark_page = watermark.pages[0]

    for page in reader.pages:
        page.merge_page(watermark_page)
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    Path(temp_watermark).unlink(missing_ok=True)


def protect_pdf(input_pdf, output_pdf, user_password, owner_password):
    with pikepdf.open(input_pdf) as pdf:
        pdf.save(
            output_pdf,
            encryption=pikepdf.Encryption(
                user=user_password,
                owner=owner_password,
                R=6,  # AES-256
                allow=pikepdf.Permissions(
                    extract=False,  # 禁止复制内容
                    print_lowres=False,
                    print_highres=False,
                    modify_annotation=False,
                    modify_assembly=False,
                    modify_form=False,
                    modify_other=False,
                ),
            ),
        )


if __name__ == "__main__":
    source_pdf = "jonas_resume.pdf"

    watermarked_pdf = "resume_watermarked.pdf"

    final_pdf = "resume_secure.pdf"

    add_watermark(
        input_pdf=source_pdf,
        output_pdf=watermarked_pdf,
        watermark_text="仅供 张三 查看 禁止外传",
    )

    protect_pdf(
        input_pdf=watermarked_pdf,
        output_pdf=final_pdf,
        user_password="123456",
        owner_password="admin888",
    )

    print("已完成：")
    print("1. 添加动态水印")
    print("2. 设置打开密码")
    print("3. 禁止复制")
    print("4. 禁止打印")
    print("5. 禁止编辑")
