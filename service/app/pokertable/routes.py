from fastapi import APIRouter

pokertable_route = APIRouter(prefix="/pokertable",tags=["pokertable"])

@pokertable_route.get("/get")
async def get(db: DBsession) -> PokertableReadPagination:
    pokertable = await pokertable_get(request, db)
    return pokertable