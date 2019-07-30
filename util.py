import time
import os
import signal
import sys

from unicorn import UC_HOOK_INSN
from unicorn import UC_HOOK_CODE

from unicorn.x86_const import *

from capstone import *
from capstone.x86 import *

import struct

import config

cs = Cs(CS_ARCH_X86, CS_MODE_64)

FSMSR = 0xC0000100
GSMSR = 0xC0000101

MAPPED_PAGES = set()

SYSCALL_OPCODE = b'\x0f\x05'


def set_gs_base(uc, addr):
    '''
    set the GS.base hidden descriptor-register field to the given address.
    this enables referencing the gs segment on x86-64.
    '''
    return set_msr(uc, GSMSR, addr)

def get_gs_base(uc):
    '''
    fetch the GS.base hidden descriptor-register field.
    '''
    return get_msr(uc, GSMSR)

def set_fs_base(uc, addr):
    '''
    set the FS.base hidden descriptor-register field to the given address.
    this enables referencing the fs segment on x86-64.
    '''
    return set_msr(uc, FSMSR, addr)

def get_fs_base(uc):
    '''
    fetch the FS.base hidden descriptor-register field.
    '''
    return get_msr(uc, FSMSR)

def set_msr(uc, msr, value, scratch=config.SCRATCH_ADDR):
    '''
    set the given model-specific register (MSR) to the given value.
    this will clobber some memory at the given scratch address, as it emits some code.
    '''
    # save clobbered registers
    orax = uc.reg_read(UC_X86_REG_RAX)
    ordx = uc.reg_read(UC_X86_REG_RDX)
    orcx = uc.reg_read(UC_X86_REG_RCX)
    orip = uc.reg_read(UC_X86_REG_RIP)

    # x86: wrmsr
    buf = b'\x0f\x30'
    uc.mem_write(scratch, buf)
    uc.reg_write(UC_X86_REG_RAX, value & 0xFFFFFFFF)
    uc.reg_write(UC_X86_REG_RDX, (value >> 32) & 0xFFFFFFFF)
    uc.reg_write(UC_X86_REG_RCX, msr & 0xFFFFFFFF)
    uc.emu_start(scratch, scratch+len(buf), count=1)

    # restore clobbered registers
    uc.reg_write(UC_X86_REG_RAX, orax)
    uc.reg_write(UC_X86_REG_RDX, ordx)
    uc.reg_write(UC_X86_REG_RCX, orcx)
    uc.reg_write(UC_X86_REG_RIP, orip)

def get_msr(uc, msr, scratch=config.SCRATCH_ADDR):
    '''
    fetch the contents of the given model-specific register (MSR).
    this will clobber some memory at the given scratch address, as it emits some code.
    '''
    # save clobbered registers
    orax = uc.reg_read(UC_X86_REG_RAX)
    ordx = uc.reg_read(UC_X86_REG_RDX)
    orcx = uc.reg_read(UC_X86_REG_RCX)
    orip = uc.reg_read(UC_X86_REG_RIP)

    # x86: rdmsr
    buf = b'\x0f\x32'
    uc.mem_write(scratch, buf)
    uc.reg_write(UC_X86_REG_RCX, msr & 0xFFFFFFFF)
    uc.emu_start(scratch, scratch+len(buf), count=1)
    eax = uc.reg_read(UC_X86_REG_EAX)
    edx = uc.reg_read(UC_X86_REG_EDX)

    # restore clobbered registers
    uc.reg_write(UC_X86_REG_RAX, orax)
    uc.reg_write(UC_X86_REG_RDX, ordx)
    uc.reg_write(UC_X86_REG_RCX, orcx)
    uc.reg_write(UC_X86_REG_RIP, orip)

    return (edx << 32) | (eax & 0xFFFFFFFF)

def load_registers(uc):
    uc.reg_write(UC_X86_REG_RAX, fetch_register("rax"))
    uc.reg_write(UC_X86_REG_RBX, fetch_register("rbx"))
    uc.reg_write(UC_X86_REG_RCX, fetch_register("rcx"))
    uc.reg_write(UC_X86_REG_RDX, fetch_register("rdx"))
    uc.reg_write(UC_X86_REG_RSI, fetch_register("rsi"))
    uc.reg_write(UC_X86_REG_RDI, fetch_register("rdi"))
    uc.reg_write(UC_X86_REG_R8, fetch_register("r8"))
    uc.reg_write(UC_X86_REG_R9, fetch_register("r9"))
    uc.reg_write(UC_X86_REG_R10, fetch_register("r10"))
    uc.reg_write(UC_X86_REG_R11, fetch_register("r11"))
    uc.reg_write(UC_X86_REG_R12, fetch_register("r12"))
    uc.reg_write(UC_X86_REG_R13, fetch_register("r13"))
    uc.reg_write(UC_X86_REG_R14, fetch_register("r14"))
    uc.reg_write(UC_X86_REG_R15, fetch_register("r15"))
    uc.reg_write(UC_X86_REG_RSP, fetch_register("rsp"))
    uc.reg_write(UC_X86_REG_RBP, fetch_register("rbp"))
    uc.reg_write(UC_X86_REG_RIP, fetch_register("rip"))
    uc.reg_write(UC_X86_REG_EFLAGS, fetch_register("eflags"))
    uc.reg_write(UC_X86_REG_DS, fetch_register("ds"))
    uc.reg_write(UC_X86_REG_CS, fetch_register("cs"))
    uc.reg_write(UC_X86_REG_ES, fetch_register("es"))
    uc.reg_write(UC_X86_REG_FS, fetch_register("fs"))
    uc.reg_write(UC_X86_REG_FS, fetch_register("gs"))
    uc.reg_write(UC_X86_REG_SS, fetch_register("ss"))

    # gs base stuff is always strange. Better map now than be sorry.
    #map_page_blocking(uc, fetch_register("gs")

    uc.mem_map(config.SCRATCH_ADDR, config.SCRATCH_SIZE)

    set_gs_base(uc, fetch_register("gs_base"))
    #print("setting gs_base to "+hex(gs))
    set_fs_base(uc, fetch_register("fs_base"))
    #print("setting fs_base to "+hex(gs))
    sys.stdout.flush() # otherwise children will inherit the unflushed buffer


def fetch_register(name):
    with open(os.path.join(config.WORKDIR, "state", name), "r") as f:
        return int(f.read())

def _base_address(address):
    return address - address % 0x1000


def fix_cmpexchg16(uc, base_address):
    """
    We replace all compxchg16bs and exits with syscalls since they should be rare in kernel code.
    Then when we encounter a syscall, we figure out which of the two (three?) occurred.
    """
    for bad_addr in config.CMPEXCHG16B_ADDRS.keys():
        if _base_address(bad_addr) == base_address:
            print("Overwriting {0:x} with syscall insn (base: {1:x})".format(bad_addr, _base_address(bad_addr)))
            uc.mem_write(bad_addr, SYSCALL_OPCODE)
    for end_addr in config.EXITS.keys():
        if _base_address(end_addr) == base_address:
            print("Setting exit {0:x}".format(end_addr))
            uc.mem_write(end_addr, SYSCALL_OPCODE)


def map_page_blocking(uc, address, workdir=config.WORKDIR):
    """
    Maps a page at addr in the harness, asking probe_wrapper.
    """
    base_address = _base_address(address)
    input_file_name = os.path.join(workdir, "requests", "{0:016x}".format(address))
    dump_file_name = os.path.join(workdir, "state", "{0:016x}".format(base_address))
    global MAPPED_PAGES
    if base_address not in MAPPED_PAGES:
        if os.path.isfile(dump_file_name + ".rejected"):
            print("CAN I HAZ EXPLOIT?")
            os.kill(os.getpid(), signal.SIGSEGV)
        if not os.path.isfile(dump_file_name):
            open(input_file_name, 'a').close()
        print("mapping {}".format(hex(base_address)))
        while 1:
            try:
                if os.path.isfile(dump_file_name + ".rejected"):
                    print("CAN I HAZ EXPLOIT?")
                    os.kill(os.getpid(), signal.SIGSEGV)
                with open(dump_file_name, "rb") as f:
                    content = f.read()
                    if len(content) < 0x1000:
                        time.sleep(0.001)
                        continue
                    uc.mem_map(base_address, len(content))
                    uc.mem_write(base_address, content)
                    MAPPED_PAGES.add(base_address)
                    #fix_cmpexchg16(uc, base_address)
                    return
            except IOError:
                pass
            except Exception as e: #todo this shouldn't happen if we don't map like idiots
                print(e)
                print("map_page_blocking failed: base address={0:016x}".format(base_address))
                #exit(1)

def map_known_mem(uc, workdir=config.WORKDIR):
    for filename in os.listdir(os.path.join(workdir, "state")):
        if not filename.endswith(".rejected"):
            try:
                address = int(filename, 16)
                map_page_blocking(uc, address)
            except:
                pass
