from fastapi import Request

from backend.session import PrinterSession


def get_session(request: Request) -> PrinterSession:
    return request.app.state.session
