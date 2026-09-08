"""DHCP integration for PXE/iPXE network boot profiles."""
from __future__ import annotations

from typing import Any

from .mini_services import DhcpService
from .network_boot import pxe_dhcp_values


class BootAwareDhcpService(DhcpService):
    """Add architecture-aware PXE boot options to normal DHCP replies."""

    def _reply(
        self,
        request_values: tuple[Any, ...],
        request_options: dict[int, bytes],
        message_type: int,
        assigned: str,
    ) -> bytes:
        old_boot = self.config.get("boot_file", "")
        old_tftp = self.config.get("tftp_server", "")
        old_next = self.config.get("next_server", "")
        try:
            tftp_server, boot_file = pxe_dhcp_values(request_options, self.config_path)
            if boot_file:
                self.config["boot_file"] = boot_file
                if boot_file.startswith(("http://", "https://")):
                    # iPXE follows the HTTP URL directly. Do not advertise an
                    # unnecessary TFTP server in option 66 for this stage.
                    self.config["tftp_server"] = ""
                else:
                    server = tftp_server or self.server_ip
                    self.config["tftp_server"] = server
                    self.config["next_server"] = server
            return super()._reply(request_values, request_options, message_type, assigned)
        finally:
            self.config["boot_file"] = old_boot
            self.config["tftp_server"] = old_tftp
            self.config["next_server"] = old_next
