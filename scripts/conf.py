#!/usr/bin/env python3

# Copyright (C) SchedMD LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Optional
from itertools import chain
from collections import defaultdict
import json
from pathlib import Path
import util
from util import dirs, slurmdirs

FILE_PREAMBLE = """
# Warning:
# This file is managed by a script. Manual modifications will be overwritten.
"""

login_nodeset = "x-login"


def dict_to_conf(conf, delim=" ") -> str:
    """convert dict to delimited slurm-style key-value pairs"""

    def filter_conf(pair):
        k, v = pair
        if isinstance(v, list):
            v = ",".join(el for el in v if el is not None)
        return k, (v if bool(v) or v == 0 else None)

    return delim.join(
        f"{k}={v}" for k, v in map(filter_conf, conf.items()) if v is not None
    )


def conflines(cloud_parameters, lkp: util.Lookup) -> str:
    scripts_dir = lkp.cfg.install_dir or dirs.scripts
    no_comma_params = cloud_parameters.no_comma_params or False

    any_gpus = any(
        lkp.template_info(nodeset.instance_template).gpu_count > 0
        for nodeset in lkp.cfg.nodeset.values()
    )

    any_tpu = any(
        tpu_nodeset is not None
        for part in lkp.cfg.partitions.values()
        for tpu_nodeset in part.partition_nodeset_tpu
    )

    any_dynamic = any(bool(p.partition_feature) for p in lkp.cfg.partitions.values())
    comma_params = {
        "PrivateData": [
            "cloud",
        ],
        "LaunchParameters": [
            "enable_nss_slurm",
            "use_interactive_step",
        ],
        "SlurmctldParameters": [
            "cloud_reg_addrs" if any_dynamic or any_tpu else "cloud_dns",
            "enable_configless",
            "idle_on_node_suspend",
        ],
        "SchedulerParameters": [
            "bf_continue",
            "salloc_wait_nodes",
            "ignore_prefer_validation",
        ],
        "GresTypes": [
            "gpu" if any_gpus else None,
        ],
    }
    prolog_path = Path(dirs.custom_scripts / "prolog.d")
    epilog_path = Path(dirs.custom_scripts / "epilog.d")
    default_tree_width = 65533 if any_dynamic else None
    conf_options = {
        **(comma_params if not no_comma_params else {}),
        "Prolog": f"{prolog_path}/*" if lkp.cfg.prolog_scripts else None,
        "Epilog": f"{epilog_path}/*" if lkp.cfg.epilog_scripts else None,
        "SuspendProgram": f"{scripts_dir}/suspend.py",
        "ResumeProgram": f"{scripts_dir}/resume.py",
        "ResumeFailProgram": f"{scripts_dir}/suspend.py",
        "ResumeRate": cloud_parameters.get("resume_rate", 0),
        "ResumeTimeout": cloud_parameters.get("resume_timeout", 300),
        "SuspendRate": cloud_parameters.get("suspend_rate", 0),
        "SuspendTimeout": cloud_parameters.get("suspend_timeout", 300),
        "TreeWidth": cloud_parameters.get("tree_width", default_tree_width),
        "JobSubmitPlugins": "lua" if any_tpu else None,
        "TopologyPlugin": cloud_parameters.get("topology_plugin", "topology/tree"),
    }
    return dict_to_conf(conf_options, delim="\n")


def loginlines() -> str:
    nodeset = {
        "NodeSet": login_nodeset,
        "Feature": login_nodeset,
    }
    partition = {
        "PartitionName": login_nodeset,
        "Nodes": login_nodeset,
        "State": "UP",
        "DefMemPerCPU": 1,
        "Hidden": "YES",
        "RootOnly": "YES",
    }
    lines = [
        dict_to_conf(nodeset),
        dict_to_conf(partition),
    ]
    return "\n".join(lines)


def nodeset_lines(nodeset, lkp: util.Lookup) -> str:
    template_info = lkp.template_info(nodeset.instance_template)
    machine_conf = lkp.template_machine_conf(nodeset.instance_template)

    # follow https://slurm.schedmd.com/slurm.conf.html#OPT_Boards
    # by setting Boards, SocketsPerBoard, CoresPerSocket, and ThreadsPerCore
    node_def = dict_to_conf(
        {
            "NodeName": "DEFAULT",
            "State": "UNKNOWN",
            "RealMemory": machine_conf.memory,
            "Boards": machine_conf.boards,
            "SocketsPerBoard": machine_conf.sockets_per_board,
            "CoresPerSocket": machine_conf.cores_per_socket,
            "ThreadsPerCore": machine_conf.threads_per_core,
            "CPUs": machine_conf.cpus,
            **nodeset.node_conf,
        }
    )

    gres = None
    if template_info.gpu_count:
        gres = f"gpu:{template_info.gpu_count}"

    lines = [node_def]
    static, dynamic = lkp.nodeset_lists(nodeset)
    # static or dynamic could be None, but Nones are filtered out of the lines
    lines.extend(
        dict_to_conf(
            {
                "NodeName": nodelist,
                "State": "CLOUD",
                "Gres": gres,
            }
        )
        if nodelist is not None
        else None
        for nodelist in [static, dynamic]
    )
    lines.append(
        dict_to_conf(
            {
                "NodeSet": nodeset.nodeset_name,
                "Nodes": ",".join(filter(None, (static, dynamic))),
            }
        )
    )
    return "\n".join(filter(None, lines))


def nodeset_tpu_lines(nodeset, lkp: util.Lookup) -> str:
    assert nodeset.node_conf is not None, "nodeset needs to contain a node_conf"

    node_def = dict_to_conf(
        {
            "NodeName": "DEFAULT",
            "State": "UNKNOWN",
            **nodeset.node_conf,
        }
    )

    lines = [node_def]
    static, dynamic = lkp.nodeset_lists(nodeset)
    # static or dynamic could be None, but Nones are filtered out of the lines
    lines.extend(
        dict_to_conf(
            {
                "NodeName": nodelist,
                "State": "CLOUD",
            }
        )
        if nodelist is not None
        else None
        for nodelist in [static, dynamic]
    )
    lines.append(
        dict_to_conf(
            {
                "NodeSet": nodeset.nodeset_name,
                "Nodes": ",".join(filter(None, (static, dynamic))),
            }
        )
    )
    return "\n".join(filter(None, lines))


def nodeset_dyn_lines(nodeset):
    """generate slurm NodeSet definition for dynamic nodeset"""
    return dict_to_conf(
        {"NodeSet": nodeset.nodeset_name, "Feature": nodeset.nodeset_feature}
    )


def partitionlines(partition, lkp: util.Lookup) -> str:
    """Make a partition line for the slurm.conf"""
    MIN_MEM_PER_CPU = 100

    def defmempercpu(nodeset: str) -> int:
        template = lkp.cfg.nodeset.get(nodeset).instance_template
        machine = lkp.template_machine_conf(template)
        return max(MIN_MEM_PER_CPU, machine.memory // machine.cpus)

    defmem = min(
        map(defmempercpu, partition.partition_nodeset), default=MIN_MEM_PER_CPU
    )

    nodesets = list(
        chain(
            partition.partition_nodeset,
            partition.partition_nodeset_dyn,
            partition.partition_nodeset_tpu,
        )
    )

    is_tpu = len(partition.partition_nodeset_tpu) > 0
    is_dyn = len(partition.partition_nodeset_dyn) > 0

    oversub_exlusive = partition.enable_job_exclusive or is_tpu
    power_down_on_idle = partition.enable_job_exclusive and not is_dyn

    line_elements = {
        "PartitionName": partition.partition_name,
        "Nodes": ",".join(nodesets),
        "State": "UP",
        "DefMemPerCPU": defmem,
        "SuspendTime": 300,
        "Oversubscribe": "Exclusive" if oversub_exlusive else None,
        "PowerDownOnIdle": "YES" if power_down_on_idle else None,
        **partition.partition_conf,
    }

    return dict_to_conf(line_elements)


def suspend_exc_lines(lkp: util.Lookup) -> str:
    static_nodes = ",".join(lkp.static_nodelist())
    suspend_exc_nodes = {
        "SuspendExcNodes": static_nodes,
    }

    dyn_parts = [
        p.partition_name
        for p in lkp.cfg.partitions.values()
        if len(p.partition_nodeset_dyn) > 0
    ]
    suspend_exc_parts = {"SuspendExcParts": [login_nodeset, *dyn_parts]}

    return list(
        filter(
            None,
            [
                dict_to_conf(suspend_exc_nodes) if static_nodes else None,
                dict_to_conf(suspend_exc_parts),
            ],
        )
    )


def make_cloud_conf(lkp: util.Lookup) -> str:
    """generate cloud.conf snippet"""
    lines = [
        FILE_PREAMBLE,
        conflines(lkp.cfg.cloud_parameters, lkp),
        loginlines(),
        *(nodeset_lines(n, lkp) for n in lkp.cfg.nodeset.values()),
        *(nodeset_dyn_lines(n) for n in lkp.cfg.nodeset_dyn.values()),
        *(nodeset_tpu_lines(n, lkp) for n in lkp.cfg.nodeset_tpu.values()),
        *(partitionlines(p, lkp) for p in lkp.cfg.partitions.values()),
        *(suspend_exc_lines(lkp)),
    ]
    return "\n\n".join(filter(None, lines))


def gen_cloud_conf(lkp: util.Lookup) -> None:
    content = make_cloud_conf(lkp)

    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cloud.conf"
    conf_file.write_text(content)
    util.chown_slurm(conf_file, mode=0o644)


def install_slurm_conf(lkp: util.Lookup) -> None:
    """install slurm.conf"""
    if lkp.cfg.ompi_version:
        mpi_default = "pmi2"
    else:
        mpi_default = "none"

    conf_options = {
        "name": lkp.cfg.slurm_cluster_name,
        "control_addr": lkp.control_addr if lkp.control_addr else lkp.hostname_fqdn,
        "control_host": lkp.control_host,
        "control_host_port": lkp.control_host_port,
        "scripts": dirs.scripts,
        "slurmlog": dirs.log,
        "state_save": slurmdirs.state,
        "mpi_default": mpi_default,
    }

    conf = lkp.cfg.slurm_conf_tpl.format(**conf_options)

    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "slurm.conf"
    conf_file.write_text(conf)
    util.chown_slurm(conf_file, mode=0o644)


def install_slurmdbd_conf(lkp: util.Lookup) -> None:
    """install slurmdbd.conf"""
    conf_options = {
        "control_host": lkp.control_host,
        "slurmlog": dirs.log,
        "state_save": slurmdirs.state,
        "db_name": "slurm_acct_db",
        "db_user": "slurm",
        "db_pass": '""',
        "db_host": "localhost",
        "db_port": "3306",
    }

    if lkp.cfg.cloudsql_secret:
        secret_name = f"{lkp.cfg.slurm_cluster_name}-slurm-secret-cloudsql"
        payload = json.loads(util.access_secret_version(lkp.project, secret_name))

        if payload["db_name"] and payload["db_name"] != "":
            conf_options["db_name"] = payload["db_name"]
        if payload["user"] and payload["user"] != "":
            conf_options["db_user"] = payload["user"]
        if payload["password"] and payload["password"] != "":
            conf_options["db_pass"] = payload["password"]

        db_host_str = payload["server_ip"].split(":")
        if db_host_str[0]:
            conf_options["db_host"] = db_host_str[0]
            conf_options["db_port"] = (
                db_host_str[1] if len(db_host_str) >= 2 else "3306"
            )

    conf = lkp.cfg.slurmdbd_conf_tpl.format(**conf_options)

    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "slurmdbd.conf"
    conf_file.write_text(conf)
    util.chown_slurm(conf_file, 0o600)


def install_cgroup_conf(lkp: util.Lookup) -> None:
    """install cgroup.conf"""
    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cgroup.conf"
    conf_file.write_text(lkp.cfg.cgroup_conf_tpl)
    util.chown_slurm(conf_file, mode=0o600)


def install_jobsubmit_lua(lkp: util.Lookup) -> None:
    """install job_submit.lua if there are tpu nodes in the cluster"""
    if any(
        tpu_nodeset is not None
        for part in lkp.cfg.partitions.values()
        for tpu_nodeset in part.partition_nodeset_tpu
    ):
        conf_options = {
            "scripts_dir": lkp.cfg.slurm_scripts_dir or dirs.scripts,
        }
        conf = lkp.cfg.jobsubmit_lua_tpl.format(**conf_options)

        conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "job_submit.lua"
        conf_file.write_text(conf)
        util.chown_slurm(conf_file, 0o600)


def gen_cloud_gres_conf(lkp: util.Lookup) -> None:
    """generate cloud_gres.conf"""

    gpu_nodes = defaultdict(list)
    for nodeset in lkp.cfg.nodeset.values():
        template_info = lkp.template_info(nodeset.instance_template)
        gpu_count = template_info.gpu_count
        if gpu_count == 0:
            continue
        gpu_nodes[gpu_count].extend(filter(None, lkp.nodeset_lists(nodeset)))

    lines = [
        dict_to_conf(
            {
                "NodeName": names,
                "Name": "gpu",
                "File": "/dev/nvidia{}".format(f"[0-{i-1}]" if i > 1 else "0"),
            }
        )
        for i, names in gpu_nodes.items()
    ]
    lines.append("\n")
    content = FILE_PREAMBLE + "\n".join(lines)

    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cloud_gres.conf"
    conf_file.write_text(content)
    util.chown_slurm(conf_file, mode=0o600)


def install_gres_conf(lkp: util.Lookup) -> None:
    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cloud_gres.conf"
    gres_conf = Path(lkp.cfg.output_dir or slurmdirs.etc) / "gres.conf"
    if not gres_conf.exists():
        gres_conf.symlink_to(conf_file)
    util.chown_slurm(gres_conf, mode=0o600)


class Switch:
    """
    Represents a switch in the topology.conf file.
    """

    def __init__(
        self,
        name: str,
        nodes: Optional[str] = None,
        switches: Optional[List["Switch"]] = None,
    ):
        self.name = name
        self.nodes = nodes  # nodelist, e.g. "alpha-[0-4],beta-[14-17]"
        self.switches = switches or []

    def conf_line(self) -> str:
        d = {"SwitchName": self.name}
        if self.nodes:
            d["Nodes"] = self.nodes

        non_empty = [
            s for s in self.switches if not s.empty()
        ]  # render only non-empty sub switches
        if non_empty:
            d["Switches"] = util.to_hostlist_fast([s.name for s in non_empty])

        return dict_to_conf(d)

    def render_conf_lines(self) -> List[str]:
        if self.empty():
            return []

        lines = [self.conf_line()]
        for s in sorted(self.switches, key=lambda s: s.name):
            lines.extend(s.render_conf_lines())
        return lines

    def empty(self) -> bool:
        if self.nodes:
            return False
        return not any(not c.empty() for c in self.switches)


def tpu_nodeset_switch(nodeset: object, lkp: util.Lookup) -> Switch:
    tpuobj = util.TPU(nodeset)
    nodelists = filter(None, lkp.nodeset_lists(nodeset))  # (static, dynamic)

    switch = Switch(name=nodeset.nodeset_name)
    if tpuobj.vmcount == 1:  # Put all nodes in one switch
        switch.nodes = ",".join(nodelists)
    else:
        # Chunk nodes into sub-switches of size `vmcount`
        for nodelist in nodelists:
            nodenames = util.to_hostnames(nodelist)
            for nodes in util.chunked(nodenames, n=tpuobj.vmcount):
                sub_switch = Switch(
                    name=f"{switch.name}-{len(switch.switches)}",
                    nodes=util.to_hostlist_fast(nodes),
                )
                switch.switches.append(sub_switch)
    return switch


def nodeset_switch(nodeset: object, lkp: util.Lookup) -> Switch:
    return Switch(
        name=nodeset.nodeset_name,
        nodes=",".join(filter(None, lkp.nodeset_lists(nodeset))),
    )


def gen_topology(lkp: util.Lookup) -> List[Switch]:
    # Returns a list of "root" switches
    tpu_root = Switch(
        name="nodeset_tpu-root",
        switches=[tpu_nodeset_switch(ns, lkp) for ns in lkp.cfg.nodeset_tpu.values()],
    )

    ord_root = Switch(
        name="nodeset-root",
        switches=[nodeset_switch(ns, lkp) for ns in lkp.cfg.nodeset.values()],
    )

    return [tpu_root, ord_root]


def gen_topology_conf(lkp: util.Lookup) -> None:
    """generate slurm topology.conf from config.yaml"""
    lines = [FILE_PREAMBLE]
    for r in gen_topology(lkp):
        lines.extend(r.render_conf_lines())
    lines.append("\n")

    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cloud_topology.conf"
    conf_file.write_text("\n".join(lines))
    util.chown_slurm(conf_file, mode=0o600)


def install_topology_conf(lkp: util.Lookup) -> None:
    conf_file = Path(lkp.cfg.output_dir or slurmdirs.etc) / "cloud_topology.conf"
    topo_conf = Path(lkp.cfg.output_dir or slurmdirs.etc) / "topology.conf"
    if not topo_conf.exists():
        topo_conf.symlink_to(conf_file)
    util.chown_slurm(conf_file, mode=0o600)
