! m8285 driver: SCMP droplet Laplace-law / spurious-current measurement on listLBM
program laplace
    use imgproc2d_mod
    use image2d_mod
    use stream_mod
    use lattutil_mod
    use partppot_mod
    use ppot_mod
    use initfield_mod
    implicit none
    type(partppot2d_t),     target  :: part
    type(imageobj2d_t),     target  :: imgobj
    type(imgproc2d_t)               :: imgpro
    type(colBGKpp_t)                :: col
    type(colBGKppwall_t)            :: colw
    type(colBGKppgf_t)              :: colgf

    integer, parameter :: xlen=128, ylen=128
    integer :: rad, nsteps, ic, ijk(1:2), d, q, n
    real(kind=wp) :: rho, u(1:3), umag, rr
    real(kind=wp) :: rl, rv, nl, nv, umax, pin, pout, dp, t1, t2, G, rho0
    real(kind=wp) :: psi_in, psi_out
    character(len=32) :: arg
    type(initbubble_t) :: initbub

    call get_command_argument(1,arg); read(arg,*) rad
    call get_command_argument(2,arg); read(arg,*) nsteps

    call initD2Q9lattice()
    call imgobj%init(dims=(/xlen,ylen/))
    call imgpro%init(imageobj=imgobj,part=part)
    call imgpro%setpbc (axis=(/.false.,.true./))
    call imgpro%run()
    call imgpro%destruct()
    call imgobj%destruct()

    G    = -1.5_wp
    rho0 = 1._wp
    block
        type(potparam_t) :: pm
        pm%getpot => pot_SC
        pm%G      =  G
        pm%rho_0  =  rho0
        call col%init(tau=1._wp,potparam=pm)
        call colw%init(rho_wall=1.4_wp,potparam=pm)
        call part%setfluidcol(col)
        do n = 1, part%nslists
            select case (trim(part%slists(n)%pt%name))
            case ('wall','lefwall','rigwall','topwall','botwall','wallf','wallb')
                call part%slists(n)%pt%alloccollision(colw)
            case ('ghostf','ghostfluid')
                call part%slists(n)%pt%alloccollision(colgf)
            end select
        end do
    end block

    call initbub%init(ijkref=(/xlen/2,ylen/2/),rho_in=0.2_wp,rho_out=1.4_wp,&
                      rad=rad,interface_width=3, &
                      lowbound = (/1,1/),upbound=(/xlen,ylen/))
    do n = 1, part%nslists
        call initbub%run(slist = part%slists(n)%pt)
    end do

    call cpu_time(t1)
    do ic = 1, nsteps
        call part%stream
        call part%collide
    end do
    call cpu_time(t2)

    ! ---- diagnostics over the real domain cells ----
    rl = 0._wp; rv = 0._wp; nl = 0._wp; nv = 0._wp; umax = 0._wp
    do ic = part%main%ist, part%main%ind
        ijk(1) = mod(part%main%rcell(ic), xlen+2)
        ijk(2) = part%main%rcell(ic)/(xlen+2)
        if (ijk(1)<1 .or. ijk(1)>xlen .or. ijk(2)<1 .or. ijk(2)>ylen) cycle
        rho = sum(part%main%pdf(0:nq_1,ic))
        if (rho < 1.e-8_wp) cycle
        do d = 1, numd
            u(d) = sum(cqvec(1:nq_1,d)*part%main%pdf(1:nq_1,ic))/rho
        end do
        umag = sqrt(sum(u(1:numd)**2))
        rr = sqrt(real(ijk(1)-xlen/2,wp)**2 + real(ijk(2)-ylen/2,wp)**2)
        if (rr > real(rad+7,wp)) then
            rl = rl + rho; nl = nl + 1._wp
        elseif (rr < real(rad-7,wp)) then
            rv = rv + rho; nv = nv + 1._wp
        endif
        if (rr > real(rad-4,wp) .and. rr < real(rad+4,wp)) umax = max(umax,umag)
    end do
    rl = rl/nl; rv = rv/nv

    ! pressure with SC equation of state p = rho/3 + (G_eff/2) psi^2
    psi_in  = rho0*(1._wp - exp(-rv/rho0))
    psi_out = rho0*(1._wp - exp(-rl/rho0))
    pin  = rv/3._wp + 0.5_wp*G*psi_in**2
    pout = rl/3._wp + 0.5_wp*G*psi_out**2
    dp   = pin - pout

    write(*,'(A,8F14.6)') 'CSV,', real(rad,wp), rl, rv, rl/rv, umax, dp, (t2-t1)
    write(*,'(A,3F14.6)') 'PRESSURE,', pin, pout, dp
    do n = 1, part%nslists
        write(*,'(A,A,A,I8)') 'LIST,', trim(part%slists(n)%pt%name), ',', part%slists(n)%pt%getnumocells()
    end do
    write(*,'(A,I10,A,F10.3,A)') 'TIMING, cells*steps=', (xlen*ylen)*nsteps, ' t=', t2-t1, ' s'
end program laplace
