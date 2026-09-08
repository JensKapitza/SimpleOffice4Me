"""Architecture-aware PXE/iPXE DHCP integration without Flask imports."""
from __future__ import annotations

from typing import Any

from simpleoffice_mini_services import DhcpService
from simpleoffice_network_boot import pxe_dhcp_values


class BootAwareDhcpService(DhcpService):
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
