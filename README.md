# qrcodemail

Python CSV->QRCode->Email attachment pipeline with web API.

## Features

- reads CSV with keys `fullname`, `email`, `phone`
- generates personal QR codes (PNG)
- attaches each QR code to a personalized email
- sends via SMTP server
- **NEW**: REST API for dynamic web form integration
- **NEW**: Landing page at root URL describing the app
- **NEW**: HTML templated emails with Jinja2 placeholders
- **NEW**: Landing page describing the service
- **NEW**: HTML templated emails support

## Setup

```powershell
cd C:\Users\imoni\Documents\JS-Projects\qrcodemail
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` with:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASS=app-password
FROM_EMAIL=you@example.com
```

## Usage

### Batch processing (CSV)

```powershell
python generate_and_send.py --csv sample_contacts.csv --subject "Your QR Code" --body "Hello {fullname},\n\nattached is your QR code." --html-body "<h1>Hello {fullname}</h1><p>attached is your QR code.</p>" --dry-run
```

### Web API (for dynamic forms)

Start the server:

```powershell
python app.py
```

Send POST to `http://localhost:5000/generate-qr-email`:

```json
{
  "fullname": "John Doe",
  "email": "john@example.com",
  "phone": "+1234567890",
  "subject": "Your Event QR Code",
  "body": "Hello {fullname},\n\nWelcome to Event2026!\n\nYour QR code is attached.",
  "html_body": "<h1>Hello {fullname}</h1><p>Welcome to Event2026!</p><p>Your QR code is attached.</p>"
}
```

## Deployment

### Railway

1. Connect GitHub repo
2. Set environment variables in dashboard
3. Deploy automatically

### Local testing

```powershell
python app.py
# Server runs on http://localhost:5000
# Visit http://localhost:5000 for the landing page
```

## Web Form Integration

Your web form should POST JSON to the `/generate-qr-email` endpoint:

```javascript
// Example fetch call
fetch('https://your-deployed-app.com/generate-qr-email', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    fullname: formData.name,
    email: formData.email,
    phone: formData.phone,
  }),
})
```

## CSV format

```csv
fullname,email,phone
Alice Johnson,alice@example.com,+15551234567
```
