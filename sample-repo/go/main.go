package main

import "log"

func main() {
	cfg := NewConfig()
	srv := NewServer(cfg)
	if err := srv.Run(); err != nil {
		log.Fatal(err)
	}
}
