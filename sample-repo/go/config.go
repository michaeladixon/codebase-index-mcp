package main

import "time"

const DefaultPort = 8080
const ShutdownGrace = 5 * time.Second

// Config holds server settings.
type Config struct {
	Port    int
	Verbose bool
}

// NewConfig returns defaults.
func NewConfig() *Config {
	return &Config{Port: DefaultPort}
}
