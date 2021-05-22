from seedemu.core import ScopedRegistry, Node, Interface, Network, Emulator, Layer
from typing import List, Dict, Set, Tuple
from ipaddress import IPv4Network

RoutingFileTemplates: Dict[str, str] = {}

RoutingFileTemplates["protocol"] = """\
protocol {protocol} {name} {{{body}}}
"""

RoutingFileTemplates["pipe"] = """\
protocol pipe {{
    table {src};
    peer table {dst};
    import {importFilter};
    export {exportFilter};
}}
"""

RoutingFileTemplates["rs_bird"] = """\
router id {routerId};
protocol device {{
}}
"""

RoutingFileTemplates["rnode_bird"] = """\
router id {routerId};
ipv4 table t_direct;
protocol device {{
}}
protocol kernel {{
    ipv4 {{
        import all;
        export all;
    }};
    learn;
}}
"""

RoutingFileTemplates['rnode_bird_direct'] = """
    ipv4 {{
        table t_direct;
        import all;
    }};
{interfaces}
"""

RoutingFileTemplates["rnode_bird_direct_interface"] = """
    interface "{interfaceName}";
"""

class Router(Node):
    """!
    @brief Node extension class.

    Nodes with routing install will be replaced with this to get the extension
    methods.
    """

    __loopback_address: str

    def setLoopbackAddress(self, address: str):
        """!
        @brief Set loopback address.

        @param address address.
        """
        self.__loopback_address = address

    def getLoopbackAddress(self) -> str:
        """!
        @brief Get loopback address.

        @returns address.
        """
        return self.__loopback_address

    def addProtocol(self, protocol: str, name: str, body: str):
        """!
        @brief Add a new protocol to BIRD on the given node.

        @param protocol protocol type. (e.g., bgp, ospf)
        @param name protocol name.
        @param body protocol body.
        """
        self.appendFile("/etc/bird/bird.conf", RoutingFileTemplates["protocol"].format(
            protocol = protocol,
            name = name,
            body = body
        ))

    def addTablePipe(self, src: str, dst: str = 'master4', importFilter: str = 'none', exportFilter: str = 'all', ignoreExist: bool = True):
        """!
        @brief add a new routing table pipe.
        
        @param src src table.
        @param dst (optional) dst table (default: master4)
        @param importFilter (optional) filter for importing from dst table to src table (default: none)
        @param exportFilter (optional) filter for exporting from src table to dst table (default: all)
        @param ignoreExist (optional) assert check if table exists. If true, error is silently discarded.

        @throws AssertionError if pipe between two tables already exist and ignoreExist is False.
        """
        meta = self.getAttribute('__routing_layer_metadata', {})
        if 'pipes' not in meta: meta['pipes'] = {}
        pipes = meta['pipes']
        if src not in pipes: pipes[src] = []
        if dst in pipes[src]:
            assert ignoreExist, 'pipe from {} to {} already exist'.format(src, dst)
            return
        pipes[src].append(dst)
        self.appendFile('/etc/bird/bird.conf', RoutingFileTemplates["pipe"].format(
            src = src,
            dst = dst,
            importFilter = importFilter,
            exportFilter = exportFilter
        ))

    def addTable(self, tableName: str):
        """!
        @brief Add a new routing table to BIRD on the given node.

        @param tableName name of the new table.
        """
        meta = self.getAttribute('__routing_layer_metadata', {})
        if 'tables' not in meta: meta['tables'] = []
        tables = meta['tables']
        if tableName not in tables: self.appendFile('/etc/bird/bird.conf', 'ipv4 table {};\n'.format(tableName))
        tables.append(tableName)

class Routing(Layer):
    """!
    @brief The Routing layer.

    This layer provides routing support for routers and hosts. i.e., (1) install
    BIRD on router nodes and allow BGP/OSPF to work, (2) setup kernel and device
    protocols, and (3) setup defult routes for host nodes.

    When this layer is rendered, two new methods will be added to the router
    node and can be used by other layers: (1) addProtocol: add new protocol
    block to BIRD, and (2) addTable: add new routing table to BIRD.
    
    This layer also assign loopback address for iBGP/LDP, etc., for other
    protocols to use later and as router id.
    """

    __direct_nets: Set[Tuple[str, str]]
    __loopback_assigner: IPv4Network
    __loopback_pos: int
    
    def __init__(self, loopback_range: str = '10.0.0.0/16'):
        """!
        @brief Routing layre constructor.

        @param loopback_range (optional) network range for assiging loopback
        IP addresses.
        """
        super().__init__()
        self.__direct_nets = set()
        self.__loopback_assigner = IPv4Network(loopback_range)
        self.__loopback_pos = 1
        self.addDependency('Base', False, False)
    
    def getName(self) -> str:
        return "Routing"

    def __installBird(self, node: Node):
        """!
        @brief Install bird on node, and handle the bug.
        """
        node.addBuildCommand('mkdir -p /usr/share/doc/bird2/examples/')
        node.addBuildCommand('touch /usr/share/doc/bird2/examples/bird.conf')
        node.addBuildCommand('apt-get install -y --no-install-recommends bird2')

    def configure(self, emulator: Emulator):
        reg = emulator.getRegistry()
        for ((scope, type, name), obj) in reg.getAll().items():
            if type == 'rs':
                rs_node: Node = obj
                self.__installBird(rs_node)
                rs_node.appendStartCommand('[ ! -d /run/bird ] && mkdir /run/bird')
                rs_node.appendStartCommand('bird -d', True)
                self._log("Bootstraping bird.conf for RS {}...".format(name))

                rs_ifaces = rs_node.getInterfaces()
                assert len(rs_ifaces) == 1, "rs node {} has != 1 interfaces".format(rs_node.getName())

                rs_iface = rs_ifaces[0]

                if not issubclass(rs_node.__class__, Router): rs_node.__class__ = Router
                rs_node.setFile("/etc/bird/bird.conf", RoutingFileTemplates["rs_bird"].format(
                    routerId = rs_iface.getAddress()
                ))
                
            if type == 'rnode':
                rnode: Router = obj
                if not issubclass(rnode.__class__, Router): rnode.__class__ = Router

                self._log("Setting up loopback interface for AS{} Router {}...".format(scope, name))

                lbaddr = self.__loopback_assigner[self.__loopback_pos]

                rnode.appendStartCommand('ip li add dummy0 type dummy')
                rnode.appendStartCommand('ip li set dummy0 up')
                rnode.appendStartCommand('ip addr add {}/32 dev dummy0'.format(lbaddr))
                rnode.setLoopbackAddress(lbaddr)
                self.__loopback_pos += 1

                self._log("Bootstraping bird.conf for AS{} Router {}...".format(scope, name))

                self.__installBird(rnode)

                r_ifaces = rnode.getInterfaces()
                assert len(r_ifaces) > 0, "router node {}/{} has no interfaces".format(rnode.getAsn(), rnode.getName())

                ifaces = ''
                has_localnet = False

                for iface in r_ifaces:
                    net = iface.getNet()
                    if (scope, net.getName()) in self.__direct_nets:
                        has_localnet = True
                        ifaces += RoutingFileTemplates["rnode_bird_direct_interface"].format(
                            interfaceName = net.getName()
                        )

                rnode.setFile("/etc/bird/bird.conf", RoutingFileTemplates["rnode_bird"].format(
                    routerId = rnode.getLoopbackAddress()
                ))

                rnode.appendStartCommand('[ ! -d /run/bird ] && mkdir /run/bird')
                rnode.appendStartCommand('bird -d', True)
                
                if has_localnet: rnode.addProtocol('direct', 'local_nets', RoutingFileTemplates['rnode_bird_direct'].format(interfaces = ifaces))

            if type == 'hnode':
                hnode: Node = obj
                hifaces: List[Interface] = hnode.getInterfaces()
                assert len(hifaces) == 1, 'Host {} in as{} has != 1 interfaces'.format(name, scope)
                hif = hifaces[0]
                hnet: Network = hif.getNet()
                rif: Interface = None

                cur_scope = ScopedRegistry(scope, reg)
                for router in cur_scope.getByType('rnode'):
                    if rif != None: break
                    for riface in router.getInterfaces():
                        if riface.getNet() == hnet:
                            rif = riface
                            break
                
                assert rif != None, 'Host {} in as{} in network {}: no router'.format(name, scope, hnet.getName())
                self._log("Setting default route for host {} ({}) to router {}".format(name, hif.getAddress(), rif.getAddress()))
                hnode.appendStartCommand('ip rou del default 2> /dev/null')
                hnode.appendStartCommand('ip route add default via {} dev {}'.format(rif.getAddress(), rif.getNet().getName()))

    def render(self, emulator: Emulator) -> None:
        pass

    def addDirect(self, asn: int, netname: str):
        """!
        @brief Add a network to "direct" protcol by name.

        Mark network as "direct" candidate. All router nodes connected to this
        network will add the interface attaches to this network to their
        "direct" protocol block.

        @param asn ASN.
        @param netname network name.
        @throws AssertionError if net not exist.
        @throws AssertionError if try to add non-AS network as direct.
        """
        self.__direct_nets.add((str(asn), netname))

    def getDirects(self) -> Set[Tuple[str, str]]:
        """!
        @brief Get the set of direct networks.

        @return set of tuple of (asn, netname)
        """
        return self.__direct_nets

    def print(self, indent: int) -> str:
        out = ' ' * indent
        out += 'RoutingLayer:\n'

        indent += 4
        out += ' ' * indent

        out += 'Direct Networks:\n'

        indent += 4

        for (asn, netname) in self.__direct_nets:
            out += ' ' * indent
            out += 'as{}/{}\n'.format(asn, netname)

        return out