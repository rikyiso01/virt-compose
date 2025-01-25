from dataclasses import dataclass, field
from bs4 import BeautifulSoup
from traceback import print_exc
from time import sleep
from pathlib import Path
from tempfile import NamedTemporaryFile
from cyclopts import App
from pydantic import TypeAdapter
from yaml import safe_load
from subprocess import check_call, check_output
from os import cpu_count, getcwd
from json import dump, loads
from contextlib import chdir

POOL = "default"

BaseTypes = str | int | None
SubArgTypes = BaseTypes | bool
SubArgs = dict[str, SubArgTypes]
ArgTypes = BaseTypes | SubArgs
ArgList = list[ArgTypes]
Args = dict[str, ArgTypes | ArgList]
Machine = Args


@dataclass
class Image:
    packerfile: Path
    output: Path
    context: Path | None = None


@dataclass
class ComposeFile:
    machines: dict[str, Machine]
    images: dict[str, Image]
    networks: list[Path] = field(default_factory=list)


def subargs_to_str(args: SubArgs):
    return ",".join(
        f"{key}={value}" if value is not None else key for key, value in args.items()
    )


def args_map_expand(args: dict[str, BaseTypes]):
    result: list[str] = []
    for key, value in args.items():
        result.append(f"--{key}")
        if value is not None:
            result.append(str(value))
    return result


def args_map_to_list(args: Args):
    return args_map_expand(
        {
            key: (subargs_to_str(value) if isinstance(value, dict) else value)
            for key, values in args.items()
            for value in (values if isinstance(values, list) else [values])
        }
    )


def build_image(
    image: Image, force: bool, only: str | None
) -> tuple[Path, bool] | None:
    build = not image.output.exists() or force
    if build:
        with chdir(image.context if image.context is not None else getcwd()):
            with NamedTemporaryFile(
                "wt", dir=image.packerfile.parent, suffix=".pkr.json"
            ) as tmpfile:
                file_content = safe_load(image.packerfile.read_text())
                dump(file_content, tmpfile)
                tmpfile.flush()
                check_call(["packer", "init", tmpfile.name])
                check_call(
                    ["packer", "build", "--force", "--parallel-builds=1"]
                    + ([f"--only={only}"] if only else [])
                    + [tmpfile.name]
                )
        if not image.output.exists():
            if only:
                return None
            assert False
    return image.output, build


def create_machine(name: str, machine: Machine, image_output: Path, force: bool):
    if not volume_exists(name) or force:
        imgsize = loads(
            check_output(["qemu-img", "info", "--output", "json", str(image_output)])
        )["virtual-size"]
        imgformat = loads(
            check_output(["qemu-img", "info", "--output", "json", str(image_output)])
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
        check_call(["virsh", "vol-upload", "--pool", POOL, name, str(image_output)])

    if not machine_exists(name) or force:
        machine.setdefault("name", name)
        machine.setdefault("vcpus", cpu_count())
        machine.setdefault("import", None)
        machine.setdefault("noautoconsole", None)
        machine.setdefault("noreboot", None)
        machine.setdefault("disk", {"vol": f"{POOL}/{name}"})
        check_call(["virt-install"] + args_map_to_list(machine))


def start_machine(name: str):
    if not machine_is_running(name):
        check_call(["virsh", "start", name])


def machine_is_running(name: str) -> bool:
    output = check_output(["virsh", "list", "--state-running"], text=True)
    return name in output


def machine_exists(name: str):
    output = check_output(["virsh", "list", "--all"], text=True)
    return name in output.split()


def volume_exists(name: str):
    output = check_output(["virsh", "vol-list", "--pool", POOL], text=True)
    return name in output.split()


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
    output = raw_output.splitlines()[-2]
    _, _, _, _, ip, _, _ = output.split()
    ip, _ = ip.split("/")
    return ip


app = App()


@app.command()
def stop_machine(name: str, timeout: int):
    if machine_is_running(name):
        check_call(["virsh", "shutdown", name, "--mode", "acpi"])
        for _ in range(timeout):
            if not machine_is_running(name):
                break
            sleep(1)
        else:
            check_call(["virsh", "destroy", name])


@app.command()
def destroy_machine(name: str):
    if machine_exists(name):
        check_call(["virsh", "undefine", name, "--nvram", "--tpm"])
    if volume_exists(name):
        check_call(["virsh", "vol-delete", "--pool", POOL, name])


@app.meta.default
def main(file: Path = Path("./virt-compose.yml")):
    global compose_file
    compose_file = TypeAdapter(ComposeFile).validate_python(safe_load(file.read_text()))


@app.command()
def build(*images_list: str, force: bool = False, only: str|None = None):
    images = [*images_list]
    if not images:
        images = [*compose_file.images]
    for name in images:
        image = compose_file.images[name]
        build_image(image, force, only)


@app.command()
def create(*machines_list: str, build: bool = False, force_recreate: bool = False):
    machines = [*machines_list]
    images: dict[str, tuple[Path, bool]] = {}
    if not machines:
        machines = [*compose_file.machines]
    for name in machines:
        machine = compose_file.machines[name]
        image = machine["image"]
        assert isinstance(image, str)
        build_result = build_image(compose_file.images[name], build, None)
        assert build_result is not None
        images[name] = build_result
    for name in machines:
        machine = compose_file.machines[name].copy()
        image_name = machine.pop("image")
        assert isinstance(image_name, str)
        image_path, should_recreate = images[image_name]
        machine = machine.copy()
        create_machine(name, machine, image_path, should_recreate or force_recreate)


@app.command()
def up(*machines_list: str, build: bool = False, force_recreate: bool = False):
    machines = [*machines_list]
    try:
        create(*machines, build=build, force_recreate=force_recreate)
        start(*machines)
    except KeyboardInterrupt:
        print_exc()


@app.command()
def start(*machines_list:str):
    machines=[*machines_list]
    if not machines:
        machines = [*compose_file.machines]
    for name in machines:
        start_machine(name)


@app.command()
def stop(*machines_list:str, timeout: int = 10):
    machines=[*machines_list]
    if not machines:
        machines = [*compose_file.machines]
    for name in machines:
        stop_machine(name, timeout)


@app.command()
def down(*machines_list:str, timeout: int = 10):
    machines=[*machines_list]
    stop(*machines, timeout=timeout)
    rm(*machines)


@app.command()
def rm(*machines_list: str):
    machines=[*machines_list]
    if not machines:
        machines = [*compose_file.machines]
    for name in machines:
        destroy_machine(name)


@app.command()
def ps(*machines_list: str, all: bool = False):
    machines=[*machines_list]
    if not machines:
        machines = [*compose_file.machines]
    for name in machines:
        print(name, machine_is_running(name) if not all else machine_exists(name))


@app.command()
def exec(machine: str, user: str, *command: str):
    ip = get_ip_address(machine)
    check_call(
        [
            "ssh",
            "-oStrictHostKeyChecking=no",
            "-oUserKnownHostsFile=/dev/null",
            f"{user}@{ip}",
        ]
        + ([" ".join(command)] if command else [])
    )


if __name__ == "__main__":
    app()
