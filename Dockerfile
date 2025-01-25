FROM docker.io/hashicorp/packer:light-1.11.2

RUN apk add qemu libvirt-client poetry virt-install

WORKDIR /app

COPY ./pyproject.toml ./poetry.lock /app/

RUN poetry install --only main --no-root

COPY virt-compose.py /app/virt-compose.py

ENTRYPOINT [ "poetry", "run", "python3", "-m", "virt-compose" ]
CMD []
