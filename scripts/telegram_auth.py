import asyncio
import os
import sys
import yaml
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

API_ID = 36752148
API_HASH = "0f91ef066beb9c6ba68a0bbdee283f4b"
PHONE = "+380635135971"
SESSION_NAME = "scripts/tg_auth_session"
HASH_FILE = "scripts/.phone_code_hash"

async def send_code():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(PHONE)
        with open(HASH_FILE, "w") as f:
            f.write(sent.phone_code_hash)
        print("CODE_SENT_SUCCESS")
        print(f"Sent type: {type(sent.type).__name__}")
        print(f"Hash saved to {HASH_FILE}")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await client.disconnect()

async def confirm_code(code: str, password: str = None):
    if not os.path.exists(HASH_FILE):
        print(f"Error: {HASH_FILE} not found. Please send code first.")
        return False
        
    with open(HASH_FILE, "r") as f:
        phone_code_hash = f.read().strip()
        
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    
    try:
        await client.sign_in(PHONE, code, phone_code_hash=phone_code_hash)
    except errors.SessionPasswordNeededError:
        if not password:
            print("2FA_PASSWORD_NEEDED")
            return False
        await client.sign_in(password=password)
    except Exception as e:
        print(f"ERROR_SIGN_IN: {e}")
        await client.disconnect()
        return False
        
    # Generate StringSession
    string_session = StringSession.save(client.session)
    print("\nSESSION_STRING_SUCCESS")
    print(string_session)
    
    # Save to file
    with open(".session_string", "w") as f:
        f.write(string_session)
        
    # Update config/config.yaml
    config_path = "config/config.yaml"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            
        if "parsers" in cfg and "telegram" in cfg["parsers"]:
            cfg["parsers"]["telegram"]["session_string"] = string_session
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            print("config/config.yaml updated with new session_string!")
            
    await client.disconnect()
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/telegram_auth.py [send | confirm <code> [password]]")
        sys.exit(1)
        
    cmd = sys.argv[1]
    if cmd == "send":
        asyncio.run(send_code())
    elif cmd == "confirm":
        if len(sys.argv) < 3:
            print("Usage: python scripts/telegram_auth.py confirm <code> [password]")
            sys.exit(1)
        code = sys.argv[2]
        pwd = sys.argv[3] if len(sys.argv) > 3 else None
        asyncio.run(confirm_code(code, pwd))
