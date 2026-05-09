# print_device_token.py
"""
Utility script to print the stored JWT device token.

Usage:
    python print_device_token.py

If no token is stored, run the Bosowa Agent (python bosowa-agent\agent\main.py)
and complete the login flow. The token will be saved in Windows Credential
Manager and can be retrieved with this script.
"""
import sys
from agent.auth.token_store import get_device_token

def main() -> None:
    token = get_device_token()
    if token:
        print(token)
    else:
        print("❌ No device token found. Please log in via the agent first.")
        sys.exit(1)

if __name__ == "__main__":
    main()
