"""GET_SOFTWARE command — enumerate installed software via registry."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def handle_get_software(payload: dict) -> dict:
    """Return list of installed software from Windows registry uninstall keys."""
    import winreg

    software_list = []
    registry_paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
    ]

    seen = set()
    for hive, path in registry_paths:
        try:
            key = winreg.OpenKey(hive, path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        name, _ = winreg.QueryValueEx(subkey, 'DisplayName')
                        if not name or name in seen:
                            raise FileNotFoundError()
                        seen.add(name)

                        entry = {'name': name}
                        for field, reg_field in [
                            ('version', 'DisplayVersion'),
                            ('publisher', 'Publisher'),
                            ('install_date', 'InstallDate'),
                            ('install_location', 'InstallLocation'),
                        ]:
                            try:
                                entry[field], _ = winreg.QueryValueEx(subkey, reg_field)
                            except FileNotFoundError:
                                entry[field] = None

                        software_list.append(entry)
                    except FileNotFoundError:
                        pass
                    finally:
                        winreg.CloseKey(subkey)
                except Exception:
                    continue
            winreg.CloseKey(key)
        except Exception:
            continue

    software_list.sort(key=lambda x: x['name'].lower())
    logger.debug('Found %d installed software entries', len(software_list))
    return {'software': software_list, 'total': len(software_list)}
