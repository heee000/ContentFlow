"""Keep raw application exceptions out of the ASGI server's fallback logger."""

from starlette.requests import Request


class ResponseStreamFailure(RuntimeError):
    """The response cannot be replaced after its headers have been sent."""


class SafeErrorBoundary:
    def __init__(self, app, *, on_error):
        self.app = app
        self.on_error = on_error

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def observed_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception as error:
            response = await self.on_error(Request(scope, receive), error)
            if not started:
                await response(scope, receive, send)
                return
        else:
            return
        # Raise outside the active exception block: a server/middleware may
        # explicitly follow __context__ despite a suppressed display chain.
        # Do not turn a truncated download into a successful response.
        raise ResponseStreamFailure("Response stream failed; inspect request diagnostics") from None
