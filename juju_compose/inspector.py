# coding=utf-8
import json
from ruamel import yaml
import utils

theme = {
    0: "normal",
    1: "green",
    2: "cyan",
    3: "red",
    4: "magenta",
    5: "yellow"
}


def scan_for(col, cur, depth):
    for e, (rel, d) in col[cur:]:
        if d and d == depth:
            return True
    return False


def get_prefix(walk, cur, depth, next_depth):
    guide = []
    for i in range(depth):
        # scan forward in walk from i seeing if a subsequent
        # entry happens at each depth
        if scan_for(walk, cur, i):
            guide.append(" │  ")
        else:
            guide.append("    ")
    if depth == next_depth:
        prefix = " ├─── "
    else:
        prefix = " └─── "
    return "{}{}".format("".join(guide), prefix)


def inspect(charm):
    tw = utils.TermWriter()
    manp = charm / ".composer.manifest"
    comp = charm / "composer.yaml"
    if not manp.exists() or not comp.exists():
        return
    manifest = json.loads(manp.text())
    composer = yaml.load(comp.open())
    a, c, d = utils.delta_signatures(manp)

    layers = set()
    for l, _, _ in manifest.values():
        layers.add(l)
    layers = list(layers)

    def get_depth(e):
        rel = e.relpath(charm)
        depth = len(rel.splitall()) - 2
        return rel, depth

    def get_suffix(rel):
        suffix = ""
        if rel in a:
            suffix = "+"
        elif rel in c:
            suffix = "*"
        return suffix

    def get_color(rel):
        # name of layer this belongs to
        color = tw.term.normal
        if rel in manifest:
            layer = manifest[rel][0]
            layerKey = layers.index(layer)
            color = getattr(tw, theme.get(layerKey, "normal"))
        else:
            if entry.isdir():
                color = tw.blue
        return color

    tw.write("Inspect %s\n" % composer["is"])
    for layer in layers:
        tw.write("# {color}{layer}{t.normal}\n",
                 color=getattr(tw, theme.get(
                     layers.index(layer), "normal")),
                 layer=layer)
    tw.write("\n")
    tw.write("{t.blue}{target}{t.normal}\n", target=charm)

    walk = sorted(utils.walk(charm, get_depth),
                    key=lambda x: x[1][0])
    for i in range(len(walk) - 1):
        entry, (rel, depth) = walk[i]
        nEnt, (nrel, ndepth) = walk[i + 1]

        tw.write("{prefix}{layerColor}{entry} "
                    "{t.bold}{suffix}{t.normal}\n",
                    prefix=get_prefix(walk, i, depth, ndepth),
                    layerColor=get_color(rel),
                    suffix=get_suffix(rel),
                    entry=rel.name)
