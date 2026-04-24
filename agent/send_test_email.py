import argparse
import sys
import os

# Add the project root to sys.path so we can import agent modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.email_handler import ResendEmailClient

def main():
    parser = argparse.ArgumentParser(description="Send a test email via Resend")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", default="Test Email from Conversion Engine", help="Email subject")
    parser.add_argument("--body", default="This is a test email sent from the CLI.", help="Email body (text)")
    parser.add_argument("--html", help="Email body (HTML)")

    args = parser.parse_args()

    client = ResendEmailClient()
    
    html_content = args.html if args.html else f"<p>{args.body}</p>"
    
    print(f"Sending email to {args.to}...")
    result = client.send_email(
        to=args.to,
        subject=args.subject,
        html=html_content,
        text=args.body
    )

    if result.get("status_code") == 200:
        print("✅ Email sent successfully!")
        print(f"ID: {result.get('response', {}).get('id')}")
    else:
        print(f"❌ Failed to send email (Status: {result.get('status_code')})")
        print(f"Response: {result.get('response') or result.get('response_text')}")

if __name__ == "__main__":
    main()
