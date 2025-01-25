from enum import StrEnum, auto
from os import close, cpu_count, pipe,environ
from typing import Any, Optional
from yaml import safe_load
from pydantic import TypeAdapter
from dataclasses import dataclass
from typer import Typer
from pathlib import Path
from subprocess import Popen, call, check_call, check_output, run as srun
from os.path import splitext
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from json import loads
from time import sleep
from string import Template, ascii_uppercase, ascii_lowercase
from bs4 import BeautifulSoup

SFTP_SERVER=environ.get('SFTP_SERVER',None)

POOL = "default"
KEYCODES = (
    {s: f"KEY_{s.upper()}" for s in ascii_lowercase}
    | {s: f"KEY_LEFTSHIFT KEY_{s}" for s in ascii_uppercase}
    | {str(n): f"KEY_{n}" for n in range(10)}
    | {
        "\n": "KEY_ENTER",
        " ": "KEY_SPACE",
        "_": "KEY_LEFTSHIFT KEY_MINUS",
        "-": "KEY_MINUS",
        "+": "KEY_LEFTSHIFT KEY_EQUAL",
        ">": "KEY_LEFTSHIFT KEY_DOT",
        ".": "KEY_DOT",
        "/": "KEY_SLASH",
        "'":"KEY_APOSTROPHE",
        '"':"KEY_LEFTSHIFT KEY_APOSTROPHE",
    }
)

class ConnectionType(StrEnum):
    ssh=auto()


@dataclass
class SleepAction:
    sleep: int


@dataclass
class TypeAction:
    type: str


@dataclass
class ScpActionContent:
    user: str
    src: str
    dst: str


@dataclass
class ScpAction:
    scp: ScpActionContent


@dataclass
class SSHActionContent:
    user: str
    command: str


@dataclass
class SSHAction:
    ssh: SSHActionContent

@dataclass
class SSHfsActionContent:
    user:str
    src:str
    dst:str

@dataclass
class SSHfsAction:
    sshfs:SSHfsActionContent

Action = SleepAction | TypeAction | ScpAction | SSHAction | SSHfsAction

ActionsList = list[Action]
ActionsFile = ActionsList

@dataclass
class CDRom:
    cdrom:str

@dataclass
class Disk:
    disk:None

Installer=CDRom|Disk


@dataclass(kw_only=True)
class BuildFile:
    location: str
    sha256: str
    memory: int
    installer:Installer
    vcpus: int | None = None
    os: str
    uefi: bool = False
    actions: ActionsList

@dataclass(kw_only=True)
class Service:
    base:Path
    operations:list[Path]

@dataclass(kw_only=True)
class ComposeFile:
    services:dict[str,Service]


@contextmanager
def download_source(location: str):
    if location.startswith("https://") or location.startswith("http://"):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            check_call(["curl", "-L", location, "-o", str(out)])
            yield out
    else:
        yield Path(location)


def ensure_source_exists(location: str, name: str, sha256: str | None):
    if not volume_exists(name):
        with download_source(location) as path:
            if sha256 is not None:
                srun(
                    ["sha256sum", "--check", "--status"],
                    check=True,
                    input=f"{sha256} {path}",
                    text=True,
                )
            imgsize = loads(
                check_output(["qemu-img", "info", "--output", "json", str(path)])
            )["virtual-size"]
            imgformat = loads(
                check_output(["qemu-img", "info", "--output", "json", str(path)])
            )["format"]
            check_call(
                [
                    "virsh",
                    "vol-create-as",
                    POOL,
                    name,
                    str(imgsize),
                    "--format",
                    imgformat,
                ]
            )
            check_call(["virsh", "vol-upload", "--pool", POOL, name, str(path)])


def get_storage_name(location: str):
    if location.startswith("https://") or location.startswith("http://"):
        return location.split("/")[-1]
    else:
        return Path(location).name


def volume_exists(name: str) -> bool:
    return call(["virsh", "vol-path", "--pool", POOL, name]) == 0


def get_ip_address(name: str) -> str:
    soup = BeautifulSoup(
        check_output(["virsh", "dumpxml", name]), features="html.parser"
    )
    (mac_tag,) = soup("mac")
    mac = mac_tag["address"]
    raw_output = check_output(
        ["virsh", "net-dhcp-leases", POOL, "--mac", mac], text=True
    )
    print(raw_output.encode())
    output=raw_output.splitlines()[-2]
    _, _, _, _, ip, _, _ = output.split()
    ip, _ = ip.split("/")
    return ip


def get_volume_path(name: str) -> Path:
    return Path(
        check_output(["virsh", "vol-path", "--pool", POOL, name], text=True).strip()
    )


def inner_args_dict_to_list(args: dict[str, object]) -> str:
    return ",".join(
        f"{key}={value}" if value is not True else key
        for key, value in args.items()
        if value not in [False, None]
    )


def send_text(name: str, text: str):
    for c in text:
        check_call(["virsh", "send-key", name, *KEYCODES[c].split()])

def is_running(name:str)->bool:
    output=check_output(['virsh','list','--state-running'],text=True)
    return name in output

def domain_exists(name:str)->bool:
    output=check_output(['virsh','list','--all'],text=True)
    return name in output

def args_dict_to_list(args: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key, value in args.items():
        if value in [False, None]:
            continue
        result.append(f"--{key}")
        if value is True:
            continue
        if isinstance(value, dict):
            result.append(inner_args_dict_to_list(value))  # type: ignore
            continue
        result.append(str(value))
    return result


app = Typer(pretty_exceptions_enable=False)


@app.command()
def build(file: Path, name: Optional[str] = None):
    if name is None:
        name = splitext(file.name)[0]
    build_file = TypeAdapter(BuildFile).validate_python(safe_load(file.read_text()))
    args: dict[str, Any] = {
        "name": name,
        "memory": build_file.memory,
        "vcpus": build_file.vcpus if build_file.vcpus is not None else cpu_count(),
        "noreboot": True,
        "os-variant": build_file.os,
        "boot": {"firmware": "efi", "loader_secure": "no"} if build_file.uefi else None,
        "disk": {"vol": f"{POOL}/{name}"},
        "noautoconsole":True
    }
    source_name=get_storage_name(build_file.location)
    ensure_source_exists(
        build_file.location, source_name, build_file.sha256
    )
    srun(['virsh','destroy',name])
    srun(['virsh','undefine',name,'--nvram','--tpm'])
    srun(['virsh','vol-delete','--pool',POOL,name])
    match build_file.installer:
        case CDRom(size):
            args["cdrom"] = get_volume_path(source_name)
            check_call(
                [
                    "virsh",
                    "vol-create-as",
                    POOL,
                    name,
                    size,
                    "--format",
                    "raw",
                ]
            )
        case Disk():
            args['import']=True
            args['noreboot']=False
    cmd = ["virt-install"] + args_dict_to_list(args)
    check_call(cmd)
    try:
        execute(name, build_file.actions)
        stop(name)
    except:
        srun(['virsh','destroy',name])
        srun(['virsh','undefine',name,'--nvram','--tpm'])
        srun(['virsh','vol-delete','--pool',POOL,name])
        raise

@app.command()
def start(name:str):
    check_call(['virsh','start',name])


@app.command()
def stop(name:str,timeout:int=10):
    if is_running(name):
        check_call(['virsh','shutdown',name,'--mode','acpi'])
        for _ in range(timeout):
            if not is_running(name):
                break
            sleep(1)
        else:
            check_call(['virsh','destroy',name])

@app.command()
def rm(name:str):
    check_call(['virsh','undefine',name,'--nvram','--tpm'])
    check_call(['virsh','vol-delete','--pool',POOL,name])

@app.command()
def ps(all:bool=False):
    args=['virsh','list']
    if all:
        args.append('--all')
    else:
        args.append('--state-running')
    check_call(args)

@app.command()
def exec(name:str,args:list[str],connection:ConnectionType=ConnectionType.ssh):
    actions:list[Action]
    match connection:
        case ConnectionType.ssh:
            user,*commands=args
            command=' '.join(commands)
            actions=[SSHAction(SSHActionContent(user=user,command=command))]
    execute(name,actions)

@app.command()
def ip(name:str):
    print(get_ip_address(name))

@app.command()
def run(name:str,file:Path):
    actions=TypeAdapter(ActionsList).validate_python(safe_load(file.read_text()))
    start(name)
    try:
        execute(name,actions)
    finally:
        stop(name)

def execute(name: str, actions: ActionsList):
    ssh_key = check_output(["ssh-add", "-L"], text=True)
    missing: set[str] = set()
    for action in actions:
        if isinstance(action, TypeAction):
            for c in Template(action.type).substitute({"SSH_KEY": ssh_key}):
                if c not in KEYCODES:
                    missing.add(c)
        if isinstance(action,SSHfsAction):
            if SFTP_SERVER is None:
                raise Exception("Missing SFTP_SERVER environment variable")
    if missing:
        raise NotImplementedError("".join(missing))
    ip: str | None = None
    processes:list[Popen[bytes]]=[]
    for action in actions:
        match action:
            case SleepAction(time):
                sleep(time)
            case TypeAction(text):
                send_text(name, Template(text).substitute({"SSH_KEY": ssh_key}))
                send_text(name, "\n")
            case ScpAction(scp):
                if ip is None:
                    ip = get_ip_address(name)
                check_call(
                    [
                        "scp",
                        "-oStrictHostKeyChecking=no",
                        "-oUserKnownHostsFile=/dev/null",
                        scp.src,
                        f"{scp.user}@{ip}:{scp.dst}",
                    ]
                )
            case SSHAction(scp):
                if ip is None:
                    ip = get_ip_address(name)
                check_call(
                    [
                        "ssh",
                        "-oStrictHostKeyChecking=no",
                        "-oUserKnownHostsFile=/dev/null",
                        f"{scp.user}@{ip}",
                        scp.command,
                    ]
                )
            case SSHfsAction(scp):
                if ip is None:
                    ip=get_ip_address(name)
                r1,w1=pipe()
                r2,w2=pipe()
                assert SFTP_SERVER is not None
                processes.append(Popen(SFTP_SERVER,stdin=r1,stdout=w2))
                processes.append(Popen(
                    [
                        "ssh",
                        "-oStrictHostKeyChecking=no",
                        "-oUserKnownHostsFile=/dev/null",
                        f"{scp.user}@{ip}",
                        'sshfs',
                        f':{scp.src}',
                        scp.dst,
                        '-oslave',
                    ],stdin=r2,stdout=w1
                    ))
                close(r1)
                close(w1)
                close(r2)
                close(w2)
                assert processes[-1].poll() is None
                sleep(1)
    for process in processes:
        process.terminate()
        process.wait()

@app.command()
def up(file:Path):
    compose_file=TypeAdapter(ComposeFile).validate_python(safe_load(file.read_text()))
    for name,service in compose_file.services.items():
        if not domain_exists(name):
            build(service.base,name=name)
            for operation in service.operations:
                run(name,operation)
        start(name)

@app.command()
def down(file:Path,volumes:bool=False):
    compose_file=TypeAdapter(ComposeFile).validate_python(safe_load(file.read_text()))
    for name,_ in compose_file.services.items():
        if domain_exists(name):
            stop(name)
            if volumes:
                rm(name)


if __name__ == "__main__":
    app()
