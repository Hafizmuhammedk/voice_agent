import asyncio

from dotenv import load_dotenv
from livekit import api


async def main():
    load_dotenv()

    request = api.ConnectTwilioCallRequest()

    fields = request.DESCRIPTOR.fields

    print("\nConnectTwilioCallRequest fields:\n")

    for field in fields:
        print(
            f"{field.name} | "
            f"type={field.type} | "
            f"message={field.message_type.full_name if field.message_type else ''}"
        )

    print("\nCurrent request:")
    print(request)

asyncio.run(main())