import time

from nostr_relay.config import Config
from nostr_relay.storage import get_storage
from nostr_relay.auth import Authenticator, Action, Role
from nostr_relay.errors import AuthenticationError
from . import BaseTestsWithStorage


class AuthTests(BaseTestsWithStorage):
    async def test_parse_options(self):
        auth = Authenticator(
            None, {"actions": {Action.save: Role.writer, Action.query: Role.reader}}
        )
        assert auth.actions == {"save": set("w"), "query": set("r")}
        auth = Authenticator(None, {})
        assert auth.actions == {"save": set("a"), "query": set("a")}

    async def test_can_perform(self):
        async with get_storage() as storage:
            auth = Authenticator(
                storage,
                {
                    "enabled": True,
                    "actions": {Action.save: Role.writer, Action.query: Role.reader},
                },
            )

            token = {"roles": set((Role.writer.value, Role.reader.value))}

            assert await auth.can_do(
                token,
                "save",
                self.make_event(
                    "f6d7c79924aa815d0d408bc28c1a23af208209476c1b7691df96f7d7b72a2753",
                    as_dict=False,
                ),
            )

            token = {"roles": set((Role.anonymous.value))}
            assert not await auth.can_do(token, "save")

    async def test_authentication(self):
        Config.authentication = {
            "actions": {Action.save: Role.writer, Action.query: Role.reader}
        }
        auth = self.storage.authenticator

        from aionostr.event import Event

        privkey1 = "f6d7c79924aa815d0d408bc28c1a23af208209476c1b7691df96f7d7b72a2753"
        pubkey1 = "5faaae4973c6ed517e7ed6c3921b9842ddbc2fc5a5bc08793d2e736996f6394d"

        challenge = "challenge"
        wrong_kind = Event(
            kind=22241,
            pubkey=pubkey1,
            created_at=time.time(),
            tags=[("relay", "ws://localhost:6969"), ("challenge", challenge)],
        )
        wrong_kind.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate("some junk", challenge)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(wrong_kind.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Wrong kind. Must be 22242."

        wrong_domain = Event(
            kind=22242,
            pubkey=pubkey1,
            created_at=time.time(),
            tags=[("relay", "ws://relay.foo.biz"), ("challenge", challenge)],
        )
        wrong_domain.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(wrong_domain.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Wrong domain: ws://relay.foo.biz"

        wrong_challenge = Event(
            kind=22242,
            pubkey=pubkey1,
            created_at=time.time(),
            tags=[("relay", "ws://localhost:6969"), ("challenge", "bad")],
        )
        wrong_challenge.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(wrong_challenge.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Wrong challenge"

        missing_tags = Event(
            kind=22242, pubkey=pubkey1, created_at=time.time(), tags=[]
        )
        missing_tags.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(missing_tags.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Missing required tags"

        too_old = Event(
            kind=22242,
            pubkey=pubkey1,
            created_at=time.time() - 605,
            tags=[("relay", "ws://localhost:6969"), ("challenge", challenge)],
        )
        too_old.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(too_old.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Too old"

        too_new = Event(
            kind=22242,
            pubkey=pubkey1,
            created_at=time.time() + 605,
            tags=[("relay", "ws://localhost:6969"), ("challenge", challenge)],
        )
        too_new.sign(privkey1)

        with self.assertRaises(AuthenticationError) as e:
            await auth.authenticate(too_new.to_json_object(), challenge)

        assert e.exception.args[0] == "invalid: Too new"

        good_event = Event(
            kind=22242,
            pubkey=pubkey1,
            created_at=time.time(),
            tags=[("relay", "ws://localhost:6969"), ("challenge", challenge)],
        )
        good_event.sign(privkey1)

        token = await auth.authenticate(good_event.to_json_object(), challenge)
        assert token["pubkey"] == pubkey1
        assert token["roles"] == set("a")

        # now create a role
        await self.storage.set_auth_roles(pubkey1, "rw")

        token = await auth.authenticate(good_event.to_json_object(), challenge)
        assert token["pubkey"] == pubkey1
        assert token["roles"] == set("rw")

    async def test_roles(self):
        pubkey1 = "5faaae4973c6ed517e7ed6c3921b9842ddbc2fc5a5bc08793d2e736996f6394d"
        pubkey2 = "5de724fcabfb5ffd14e48a18f329092f345d0d5ed9f0f02903f40ec02753b011"

        await self.storage.set_auth_roles(pubkey1, "rw")
        assert await self.storage.get_auth_roles(pubkey1) == set(["r", "w"])

        assert await self.storage.get_auth_roles(pubkey2) == set(["a"])
        await self.storage.set_auth_roles(pubkey2, "s")
        assert await self.storage.get_auth_roles(pubkey2) == set(["s"])

        all_roles = []
        async for pubkey, roles in self.storage.get_all_auth_roles():
            all_roles.append((pubkey, roles))
        assert all_roles == [(pubkey1, set(["r", "w"])), (pubkey2, set("s"))]

        await self.storage.set_auth_roles(pubkey1, "s")
