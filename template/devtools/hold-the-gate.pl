#!/usr/bin/perl
use strict;
use warnings;
use Cwd qw(getcwd);
use Fcntl qw(:flock);
use IO::Handle;

my ($lock) = @ARGV;
defined $lock or die "usage: hold-the-gate.pl LOCK, with file descriptor 9 open on LOCK\n";

open(my $held, ">>&=", 9) or die "hold-the-gate: file descriptor 9 is not open on $lock: $!\n";

unless (flock($held, LOCK_EX | LOCK_NB)) {
    open(my $record, "<", $lock) or die "hold-the-gate: cannot read $lock: $!\n";
    my $holder = <$record>;
    print STDERR defined $holder
        ? "pre-commit: waiting for the gate already running in $holder"
        : "pre-commit: waiting for the gate already running for this project\n";
    flock($held, LOCK_EX) or die "hold-the-gate: cannot lock $lock: $!\n";
}

truncate($held, 0) or die "hold-the-gate: cannot clear $lock: $!\n";
$held->autoflush(1);
print {$held} getcwd(), "\n";
