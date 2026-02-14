from functools import cached_property, lru_cache
from typing import TYPE_CHECKING, ClassVar

from fastapi_mail import ConnectionConfig as BaseConnectionConfig
from fastapi_mail import FastMail as BaseFastMail
from fastapi_mail import MessageSchema as BaseMessageSchema
from fastapi_mail import MessageType
from pydantic import ConfigDict, Field, NameEmail, computed_field, field_validator


try:
    import resend
except (ImportError, ModuleNotFoundError):
    resend = None


if TYPE_CHECKING:
    from app.config import Config


def get_app_config(copy: bool = False) -> type["Config"]:
    """
    Utility function to get app.config.Config (prevents circular imports)
    """
    from app.config import Config

    def copy_class(klass):
        return type(
            f"{klass.__name__}Copy",
            tuple(klass.__mro__[1:]),
            dict(klass.__dict__),
        )

    if copy:
        return copy_class(Config)  # type: ignore
    return Config


class Email(BaseMessageSchema):
    """
    Pydantic schema for email messages
    """

    model_config = ConfigDict(validate_by_name=True)

    recipients: list[str]
    subject: str
    template: str
    template_context: dict | None = Field(default_factory=dict)
    cc: list[str] | str = Field(default_factory=list)
    bcc: list[str] | str = Field(default_factory=list)
    reply_to: list[NameEmail | str] = Field(default_factory=list)

    # automatic defaults
    subtype: MessageType = MessageType.html

    # dump only; use from_email for input optionally with from_name
    # (if unspecified, uses defaults defined on MailConfig)
    sender: str | None = Field(alias="from", default=None)

    # dump only; stores the rendered template
    template_body: str | None = Field(alias="html", default=None)

    @field_validator("template_context", mode="after")
    @classmethod
    def add_app_config_to_template_context(cls, ctx: dict | None) -> dict | None:
        if ctx is None:
            return None

        ctx.setdefault("AppConfig", get_app_config(copy=True))
        return ctx


class MailSender:
    """
    Abstract interface for defining custom email handlers for sending MessageSchema instances
    """

    async def send_message(self, message: Email) -> None:
        raise NotImplementedError


class MailConfig(BaseConnectionConfig):
    """
    The configuration settings for FastMail. If MAIL_SENDER is not defined, uses SMTP
    """

    MAIL_SENDER: MailSender | None = None


class Resend(MailSender):
    """
    Send emails with https://resend.com
    """

    def __init__(self, api_key: str):
        if resend is None:
            raise RuntimeError("Please install the resend library")

        resend.api_key = api_key

    async def send_message(self, message: Email) -> None:
        message.template_context = None
        d = message.model_dump(mode="json", by_alias=True)
        resend.Emails.send(
            {
                "from": d["from"],
                "to": d["recipients"],
                "subject": d["subject"],
                "cc": d["cc"],
                "bcc": d["bcc"],
                "reply_to": d["reply_to"],
                "html": d["html"],
            }
        )


class FastMail(BaseFastMail):
    """ """

    def __init__(self, config: MailConfig):
        super().__init__(config)

    async def send_message(self, message: Email) -> None:
        await self.send_messages(messages=[message])

    async def send_messages(self, messages: list[Email]) -> None:
        template_env = self.config.template_engine()
        for message in messages:
            template = template_env.get_template(message.template)
            message.template_body = template.render(**message.template_context)

        if self.config.MAIL_SENDER:
            for message in messages:
                message.sender = await self._FastMail__sender(message)
                await self.config.MAIL_SENDER.send_message(message)
        else:
            prepared_messages = []
            for message in messages:
                prepared_messages.append(await self._FastMail__prepare_message(message))

            await self._FastMail__send_prepared_messages(prepared_messages)


@lru_cache
def get_fast_mail() -> FastMail:
    return FastMail(get_app_config().MAIL_CONFIG)
