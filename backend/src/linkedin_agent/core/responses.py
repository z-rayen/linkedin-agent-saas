"""The JSON response class of the application. The charset is declared, so accented text (answers, sources, job titles)
is read as UTF-8 by every client, instead of depending on the client's default."""

from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
