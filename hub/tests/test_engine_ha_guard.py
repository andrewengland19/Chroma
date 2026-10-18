# The unauthenticated config path must not be able to repoint the Home Assistant connector.
async def test_set_config_cannot_change_ha(make_engine):
    eng, _bulbs, _p = await make_engine()
    await eng.set_config({"ha": {"enabled": True, "url": "http://evil.example"}, "brightness": 0.5})
    assert eng.cfg.ha == {} and eng.cfg.brightness == 0.5
