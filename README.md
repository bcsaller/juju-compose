Juju Charm Composition

This is a Prototype designed to flush out requirements around Charm
Composition. Today its very common to fork charms for minor changes or to
have to use subordinate charms to take advantages of frameworks where you need
to deploy a custom workload to an existing runtime. With charm composition you
should be able to inherit from a charm that provides the runtime (or just some
well contained feature set) and maintain you're delta as a 'layer' that gets
composed with its base to produce a new charm.

This process should be runnable repeatedly allowing charms to be regenerated.


This work is currently feature incomplete but does allow the generation of
simple charms and useful basic composition. It is my hope that this will
encourage discussion of the feature set needed to one day have charm
composition supported natively in juju-core.


Today the system can be run as follows:

    ./juju_compose.py -o <output_repo> <output_charm_name> <charm to build from>

So you might use the included (very unrealistic) test case as like:o

    ./juju_compose -o out foo tests/trusty/tester

Running this should produce a charm in out/trusty/foo which is composed
according to the composer.yaml file in tests/trusty/tester. While this isn't
documented yet it shows some of the basic features of diverting hooks (for
pre/post hooks support), replacing files, merging metadata.yaml changes, etc.

It should be enough to give you an idea how it works. In order for this example
to run you'll need to pip install bundletester as it shares some code with that
project.
