"""Unit tests for the iDRAC Redfish client."""

import pytest
from unittest.mock import patch, MagicMock

from azure_local_deploy.idrac_client import IdracClient


@pytest.fixture
def client():
    return IdracClient("10.0.0.1", "root", "password", verify_ssl=False)


def test_url_construction(client):
    assert client._url("/Systems/System.Embedded.1") == \
        "https://10.0.0.1/redfish/v1/Systems/System.Embedded.1"


def test_url_passthrough_absolute(client):
    url = "https://other.host/redfish/v1/something"
    assert client._url(url) == url


def test_set_power_state_invalid(client):
    with pytest.raises(ValueError, match="Invalid power state"):
        client.set_power_state("InvalidState")


@patch.object(IdracClient, "get")
def test_get_power_state(mock_get, client):
    mock_get.return_value = {"PowerState": "On"}
    assert client.get_power_state() == "On"


@patch.object(IdracClient, "post")
def test_insert_virtual_media(mock_post, client):
    mock_post.return_value = MagicMock(status_code=204)
    client.insert_virtual_media("https://server/image.iso")
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "image.iso" in call_args[1].get("payload", {}).get("Image", "") or \
           "image.iso" in str(call_args)
