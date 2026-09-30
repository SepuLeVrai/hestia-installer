import json
import unittest
from browser_native import NativeBrowserTests

class Diagnostic(NativeBrowserTests):
    def setUp(self):
        super().setUp()
        self.draft_requests = []
        def request(req):
            if req.url.endswith('/api/wizard/draft') and req.method == 'POST':
                self.draft_requests.append(req.post_data_json)
        self.browser_context.on('request', request)

    def refresh(self):
        print('DRAFT_BEFORE_REFRESH', self.http('/api/wizard/state')['body'], flush=True)
        super().refresh()

    def test_draft_refresh_empty_selection_and_advanced_ref(self):
        try:
            super().test_draft_refresh_empty_selection_and_advanced_ref()
        finally:
            print('DRAFT_POST_REQUESTS', json.dumps(self.draft_requests), flush=True)
            print('DRAFT_AFTER_TEST', self.http('/api/wizard/state')['body'], flush=True)
            print('DRAFT_DOM', self.page.evaluate("() => [...document.querySelectorAll('input[id^=module-]')].map(x=>[x.id,x.checked,x.defaultChecked])"), flush=True)

suite=unittest.TestSuite(Diagnostic('test_draft_refresh_empty_selection_and_advanced_ref') for _ in range(12))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
