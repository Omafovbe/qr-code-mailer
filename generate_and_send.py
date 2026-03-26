import argparse
import csv
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import qrcode
from dotenv import load_dotenv


@dataclass
class Contact:
    fullname: str
    email: str
    phone: str


def configure_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_contacts(csv_path: Path):
    contacts = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"fullname", "email", "phone"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV header must contain: {', '.join(required)}")
        for row in reader:
            fullname = (row.get("fullname") or "").strip()
            email = (row.get("email") or "").strip()
            phone = (row.get("phone") or "").strip()
            if not fullname or not email or not phone:
                logging.warning("Skipping contact with missing data: %s", row)
                continue
            contacts.append(Contact(fullname=fullname, email=email, phone=phone))
    return contacts


def make_qr(contact: Contact, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = f"Name: {contact.fullname}\nEmail: {contact.email}\nPhone: {contact.phone}\nEvent2026"
    qr = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    sanitized = contact.email.replace("@", "_at_").replace(".", "_")
    output_file = output_dir / f"{sanitized}.png"
    img.save(output_file)
    logging.info("Generated QR code for %s -> %s", contact.email, output_file)
    return output_file


def build_message(contact: Contact, qr_path: Path, from_address: str, subject: str, body_template: str):
    body = body_template.format(fullname=contact.fullname, email=contact.email, phone=contact.phone)

    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = contact.email
    msg["Subject"] = subject
    msg.set_content(body)

    with open(qr_path, "rb") as f:
        data = f.read()
    maintype, subtype = "image", "png"
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=qr_path.name)

    return msg


def send_email(
    msg: EmailMessage,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    use_tls: bool = True,
):
    if use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
    else:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)

    try:
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        logging.info("Sent %s to %s", msg["Subject"], msg["To"])
    finally:
        server.quit()


def parse_arguments():
    parser = argparse.ArgumentParser(description="CSV to QR code email sender")
    parser.add_argument("--csv", required=True, type=Path, help="Input CSV file path")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Output folder for generated QR codes")
    parser.add_argument("--subject", default="Your QR Code", help="Email subject")
    parser.add_argument(
        "--body",
        default="Hello {fullname},\n\nPlease find your QR code attached.\n\nCheers,\nTeam",
        help="Email body template with placeholders {fullname}, {email}, {phone}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate QR and email preview without sending")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


def main():
    args = parse_arguments()
    configure_logging(args.log_level)

    load_dotenv()

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    if not smtp_host or not from_email:
        raise SystemExit("SMTP_HOST and FROM_EMAIL (or SMTP_USER) must be set in environment")

    contacts = load_contacts(args.csv)
    if not contacts:
        raise SystemExit("No valid contacts found")

    for contact in contacts:
        qr_path = make_qr(contact, args.output_dir)
        message = build_message(contact, qr_path, from_email, args.subject, args.body)

        if args.dry_run:
            logging.info("Dry run: would send %s email to %s with attachment %s", args.subject, contact.email, qr_path)
            continue

        send_email(message, smtp_host, smtp_port, smtp_user, smtp_pass, use_tls=(smtp_port == 587))


if __name__ == "__main__":
    main()
