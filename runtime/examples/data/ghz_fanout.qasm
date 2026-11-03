// the same GHZ, written with a custom CNOT (h · cz · h), a cancelling phase pair, and u2 for the Hadamard
OPENQASM 2.0;
include "qelib1.inc";
gate myh a { u2(0, pi) a; }
gate fan(theta) c, t { cu1(theta) c, t; cu1(-theta) c, t; h t; cz c, t; h t; }
qreg q[3];
myh q[0];
fan(pi/4) q[0], q[1];
cx q[1], q[2];
