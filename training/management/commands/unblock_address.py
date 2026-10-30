from django.core.management.base import BaseCommand, CommandError

from training.sign_in_protection import blocks_overview, lift_block, normalize_network


class Command(BaseCommand):
    help = "Lift a network's sign-in block and clear its counters, for when no administrator can sign in."

    def add_arguments(self, parser):
        parser.add_argument("network", nargs="?", help="An IP address or network, for example 203.0.113.7.")
        parser.add_argument("--list", action="store_true", help="List the active blocks.")

    def handle(self, *args, **options):
        if options["list"]:
            for block in blocks_overview()["active_blocks"]:
                ends = block.expires_at.isoformat() if block.expires_at else "permanent"
                self.stdout.write(f"{block.network}\t{block.failure_count} failures\tends {ends}")
            return
        if not options["network"]:
            raise CommandError("Name a network, or use --list.")
        try:
            network = normalize_network(options["network"])
        except ValueError as exc:
            raise CommandError(f"Not an IP address or network: {options['network']}") from exc
        if lift_block(network) is None:
            self.stdout.write(f"No open block for {network}; its counters are cleared.")
        else:
            self.stdout.write(f"Lifted the block on {network}.")
