# virt-compose

Declarative virtual machine building and management

## Introduction

This program builds virtual machine images using packer and adds them to libvirt.
Then you can manage them using a docker-compose like interface.

## Requirements

- libvirt installed and running
- nix

## Usage

Build and start a set of virtual machines specified in a virt-compose.yml file:

```bash
nix -- run github:rikyiso01/virt-compose up
```

## Examples

To run an example, cd into the example folder and run
```bash
nix -- run github:rikyiso01/virt-compose up
```
to build and run the machine using libvirt.
