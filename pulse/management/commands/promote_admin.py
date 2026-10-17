from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from pulse.models import UserProfile


class Command(BaseCommand):
    help = 'Set a user\'s profile role to ADMIN (same as editing it in PGAdmin).'

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError(f"User '{options['username']}' does not exist")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.Role.ADMIN
        profile.save(update_fields=['role'])
        self.stdout.write(self.style.SUCCESS(f'{user.username} is now an admin'))
