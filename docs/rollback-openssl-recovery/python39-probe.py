import io, os, time, types
from pathlib import Path
from types import SimpleNamespace
class UpdateError(RuntimeError): pass
updates=types.ModuleType("probe_updates")
updates.__dict__.update(Path=Path,os=types.SimpleNamespace(scandir=os.scandir,killpg=os.killpg),time=time,UpdateError=UpdateError)
exec('def command_group_running(group, deadline):\n    """Confirm all group members have ended, including an unreaped descendant."""\n    if not Path(\'/proc\').is_dir():\n        try:\n            os.killpg(group, 0)\n            return True\n        except ProcessLookupError:\n            return False\n    count = 0\n    with os.scandir(\'/proc\') as entries:\n        for entry in entries:\n            if time.monotonic() >= deadline:\n                raise UpdateError(\'command completion could not be confirmed in time\')\n            if not entry.name.isdigit():\n                continue\n            count += 1\n            if count > 65536:\n                raise UpdateError(\'too many processes to confirm command completion\')\n            try:\n                with (Path(entry.path) / \'stat\').open() as handle:\n                    fields = handle.read(4096).rsplit(\')\', 1)[1].split()\n                if fields[0] != \'Z\' and int(fields[2]) == group:\n                    return True\n            except (FileNotFoundError, ProcessLookupError):\n                continue\n            except (ValueError, IndexError) as error:\n                raise UpdateError(\'command completion could not be confirmed\') from error\n    return False',updates.__dict__)
def proc_entries(monkeypatch, entries):

    class StatFile(io.StringIO):

        def __init__(self, value):
            self.value = value
            super().__init__(value if isinstance(value, str) else '')

        def read(self, size=-1):
            if isinstance(self.value, BaseException):
                raise self.value
            return super().read(size)

    class ProcPath:

        def __init__(self, path):
            self.path = str(path)

        def is_dir(self):
            assert self.path == '/proc'
            return True

        def __truediv__(self, name):
            assert name == 'stat'
            return ProcPath(self.path + '/stat')

        def open(self):
            value = entries[self.path.split('/')[2]]
            return StatFile(value)

    class Scan:

        def __enter__(self):
            return iter((SimpleNamespace(name=pid, path='/proc/' + pid) for pid in entries))

        def __exit__(self, *args):
            return False
    monkeypatch.setattr(updates, 'Path', ProcPath)
    monkeypatch.setattr(updates.os, 'scandir', lambda path: Scan())
class Patch:
 def setattr(self,obj,key,value): setattr(obj,key,value)
for name,entries,want in [
 ("disappeared_then_live",{"123":ProcessLookupError(3,"gone"),"124":"124 (fixture) S 1 77 0"},True),
 ("all_disappeared",{"123":ProcessLookupError(3,"gone")},False),
 ("permission_denied",{"123":PermissionError(13,"denied")},PermissionError),
 ("malformed",{"123":"malformed"},UpdateError)]:
 proc_entries(Patch(),entries)
 try: result=updates.command_group_running(77,time.monotonic()+2)
 except Exception as e:
  assert isinstance(want,type) and isinstance(e,want),(name,type(e))
 else: assert result is want,(name,result,want)
 print(name,"PASS")
