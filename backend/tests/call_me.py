import os
from livekit import api

async def main():
    lkapi = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )

    response = await lkapi.connector.connect_twilio_call(
        api.ConnectTwilioCallRequest(
            twilio_call_direction=(
                api.ConnectTwilioCallRequest.TWILIO_CALL_DIRECTION_OUTBOUND
            ),
            room_name="ai-call-hafiz",
            participant_identity="hafiz-phone",
            participant_name="Hafiz",
            agents=[
                api.RoomAgentDispatch(
                    agent_name="general-assistant"
                )
            ],
        )
    )

    print("Connect URL:")
    print(response.connect_url)

    await lkapi.aclose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())