package main

import (
	"fmt"
	"net/http"
)

// Server wraps the http listener.
type Server struct {
	cfg *Config
}

// NewServer builds a Server from config.
func NewServer(cfg *Config) *Server {
	return &Server{cfg: cfg}
}

// Handle registers routes.
func (s *Server) Handle() {
	http.HandleFunc("/ping", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "pong")
	})
}

// Run starts listening.
func (s *Server) Run() error {
	s.Handle()
	return http.ListenAndServe(fmt.Sprintf(":%d", s.cfg.Port), nil)
}
