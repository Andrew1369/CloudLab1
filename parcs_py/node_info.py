import os
import platform


class NodeInfo:
    def __init__(self, cpu, ram):
        self.cpu, self.ram = cpu, ram

    def serialize(self):
        return {"cpu": self.cpu, "ram": self.ram}


def get_node_info_for_current_machine():
    ram = "not detected"
    if hasattr(os, "sysconf"):
        try:
            ram = f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30:.1f} GiB"
        except (ValueError, OSError):
            pass
    return NodeInfo(f"{platform.processor() or platform.machine()}; {os.cpu_count()} logical CPUs", ram)


def create_node_info(data):
    return NodeInfo(data["cpu"], data["ram"])
