import keyring
import pandas as pd
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from schema_engine.samples import generate_dirty_dataframe


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.saved = {}

    def get_password(self, service, username):
        return self.saved.get((service, username))

    def set_password(self, service, username, password):
        self.saved[(service, username)] = password

    def delete_password(self, service, username):
        if self.saved.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def memory_keyring():
    """Tests never read or write the real Credential Manager."""
    real = keyring.get_keyring()
    store = MemoryKeyring()
    keyring.set_keyring(store)
    yield store
    keyring.set_keyring(real)


@pytest.fixture
def dirty_df() -> pd.DataFrame:
    return generate_dirty_dataframe(rows=40, seed=3)
