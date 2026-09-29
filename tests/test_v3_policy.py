from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore
from app.v3_policy import PolicyFacade, Resource, can_with_legacy, contact_checker


class PolicyFacadeTests(unittest.TestCase):
    def test_unknown_action_and_resource_deny(self):
        facade=PolicyFacade(lambda principal,feature: True)
        facade.register("known",checker=lambda principal,action,resource: True)
        self.assertFalse(facade.can("alice","invented",Resource("known","1")))
        self.assertFalse(facade.can("alice","read",Resource("unknown","1")))

    def test_feature_denial_happens_before_resource_checker(self):
        calls=[]
        facade=PolicyFacade(lambda principal,feature: False)
        facade.register("contact",feature="contacts",checker=lambda *args: calls.append(args) or True)
        decision=facade.decide("alice","read",Resource("contact","1"))
        self.assertFalse(decision.allowed)
        self.assertEqual("feature_denied",decision.reason)
        self.assertEqual([],calls)

    def test_policy_exception_fails_closed(self):
        facade=PolicyFacade(lambda principal,feature: True)
        facade.register("x",checker=lambda *args: (_ for _ in ()).throw(RuntimeError("private")))
        self.assertEqual("policy_error",facade.decide("alice","read",Resource("x","1")).reason)

    def test_contact_adapter_mirrors_existing_visibility_and_management(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            store=ContactStore(root)
            contact=store.upsert({"display_name":"Alice"},"alice")
            facade=PolicyFacade(lambda principal,feature: True)
            facade.register("contact",feature="contacts",checker=contact_checker(root))
            ref=Resource("contact",contact["contact_id"])
            self.assertTrue(facade.can("alice","read",ref))
            self.assertTrue(facade.can("alice","update",ref))
            self.assertFalse(facade.can("bob","read",ref))
            self.assertFalse(facade.can("bob","update",ref))

    def test_filter_visible_does_not_return_denied_ids(self):
        allowed={"1","3"}
        facade=PolicyFacade(lambda principal,feature: True)
        facade.register("x",checker=lambda principal,action,resource: resource.id in allowed)
        result=facade.filter_visible("alice",[Resource("x",str(i)) for i in range(1,5)])
        self.assertEqual(["1","3"],[item.id for item in result])

    def test_feature_flag_can_return_to_legacy_path(self):
        facade=PolicyFacade(lambda principal,feature: True)
        facade.register("x",checker=lambda *args: False)
        ref=Resource("x","1")
        self.assertTrue(can_with_legacy(facade,"alice","read",ref,lambda: True,environ={}))
        self.assertFalse(can_with_legacy(facade,"alice","read",ref,lambda: True,environ={"SIMPLEOFFICE_V3_POLICY_ENABLED":"1"}))


if __name__=="__main__":
    unittest.main()
