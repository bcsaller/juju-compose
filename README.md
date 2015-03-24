Juju Charm Composition

This is a Prototype designed to flush out requirements around Charm
Composition. Today its very common to fork charms for minor changes or to
have to use subordinate charms to take advantages of frameworks where you need
to deploy a custom workload to an existing runtime. With charm composition you
should be able to inherit from a charm that provides the runtime (or just some
well contained feature set) and maintain you're delta as a 'layer' that gets
composed with its base to produce a new charm.

This process should be runnable repeatedly allowing charms to be regenerated.
