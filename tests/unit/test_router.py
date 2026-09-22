import unittest

from jittersim.networking.router import (
    TG3442DERouter,
    _Form,
    _FormParser,
    _wifi_enabled,
)


class RouterFormTests(unittest.TestCase):
    def test_parser_keeps_selected_wifi_controls(self) -> None:
        parser = _FormParser()
        parser.feed(
            """
            <form action=\"/php/save_wifi.php\" method=\"post\">
              <input type=\"hidden\" name=\"wifi_ssid24\" value=\"Vodafone-Test\">
              <input type=\"password\" name=\"wifi_password24\" value=\"secret\">
              <input type=\"checkbox\" name=\"wifi_on_state\" value=\"1\" checked>
              <input type=\"checkbox\" name=\"main_wifi24_state\" value=\"1\" checked>
              <select name=\"channel\"><option value=\"auto\" selected>Auto</option></select>
            </form>
            """
        )

        self.assertEqual(len(parser.forms), 1)
        form = parser.forms[0]
        self.assertEqual(form.action, "/php/save_wifi.php")
        self.assertEqual(form.controls["wifi_ssid24"], "Vodafone-Test")
        self.assertEqual(form.controls["wifi_password24"], "secret")
        self.assertEqual(form.controls["channel"], "auto")
        self.assertTrue(_wifi_enabled(form.controls))

    def test_set_wifi_enabled_posts_all_present_radio_states_and_verifies(self) -> None:
        initial = _Form(
            action="/php/save_wifi.php",
            method="post",
            controls={
                "wifi_ssid24": "Vodafone-Test",
                "wifi_password24": "secret",
                "wifi_on_state": "1",
                "main_wifi24_state": "1",
                "main_wifi5_state": "1",
            },
        )
        updated = _Form(
            action="/php/save_wifi.php",
            method="post",
            controls={
                "wifi_on_state": "0",
                "main_wifi24_state": "0",
                "main_wifi5_state": "0",
            },
        )
        router = object.__new__(TG3442DERouter)
        router.base_url = "http://192.168.0.1"
        forms = iter(((initial, ""), (updated, "")))
        requests = []
        router._wifi_form = lambda: next(forms)
        router._request = lambda method, path, **kwargs: requests.append(
            (method, path, kwargs)
        ) or ""

        settings = router.set_wifi_enabled(enabled=False)

        self.assertFalse(settings.enabled)
        self.assertEqual(requests[0][0], "post")
        self.assertEqual(requests[0][1], "http://192.168.0.1/php/save_wifi.php")
        self.assertEqual(
            {
                name: requests[0][2]["data"][name]
                for name in ("wifi_on_state", "main_wifi24_state", "main_wifi5_state")
            },
            {
                "wifi_on_state": "0",
                "main_wifi24_state": "0",
                "main_wifi5_state": "0",
            },
        )


if __name__ == "__main__":
    unittest.main()
