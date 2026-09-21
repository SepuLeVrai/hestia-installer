import unittest

from installer.model import InstallState, StepRecord


class ModelTests(unittest.TestCase):
    def test_step_serialization(self) -> None:
        step = StepRecord("preflight", InstallState.DONE, {"checks": 3})
        self.assertEqual(
            step.as_dict(),
            {
                "name": "preflight",
                "state": "DONE",
                "details": {"checks": 3},
            },
        )


if __name__ == "__main__":
    unittest.main()
